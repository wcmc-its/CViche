# Production Nodes: when a worker node goes NotReady

What to do when an EKS node in `cviche-al2023-nodes` stops reporting, and how the nodegroup is configured. The nodegroup is managed in AWS directly, not in this repo, so its settings are recorded here. Companion to [PRODUCTION_BACKUPS.md](PRODUCTION_BACKUPS.md).

## Nodegroup configuration (checked 2026-10-08)

| Setting | Value | Notes |
|---|---|---|
| Cluster / nodegroup | `reciter` / `cviche-al2023-nodes` | Backend, frontend and both worker pools are pinned to it by `nodeSelector` |
| Instances | 2 x t3.medium, ON_DEMAND | ~3.2 GiB allocatable memory each |
| Size | min = max = desired = 2 | Cluster Autoscaler cannot add a node, so pods displaced from a sick node stay Pending |
| Node auto repair | **Enabled 2026-10-08** | Was off. EKS replaces a node that stays NotReady |
| ASG health check | EC2 | Passes while the instance runs, even with kubelet dead, so the ASG alone never replaces a NotReady node |

Memory is overcommitted: workers request 512 Mi with a 2 Gi limit, and on 2026-10-08 the node's limits totalled 347% of allocatable. Resizing is #82.

## Symptoms

- Runs fail with "the server running it was shut down while it was in progress". Before #1565 the same failure read "the server was shut down for a deploy", even when no deploy happened.
- A PDF run's retry fails with "This PDF is too large or complex" although its first attempt converted the file (#1566, #1567).
- Throughput halves: worker pods sit `Terminating` on the dead node, and their replacements stay `Pending` with `Insufficient memory` / `max node group size reached`.

## Diagnose

```bash
kubectl get nodes -l eks.amazonaws.com/nodegroup=cviche-al2023-nodes
kubectl get pods -n cviche-dev -o wide          # Terminating / Pending pods and their node
kubectl get events -n cviche-dev --sort-by=.lastTimestamp | tail -20
kubectl describe node <node> | sed -n '/Conditions/,/Addresses/p'   # "Kubelet stopped posting node status"
aws eks describe-nodegroup --cluster-name reciter --nodegroup-name cviche-al2023-nodes \
  --query 'nodegroup.{scaling:scalingConfig,repair:nodeRepairConfig}'
```

A node whose EC2 status checks pass but which no longer reports to Kubernetes, with CPU still busy, points to memory exhaustion that starved kubelet. It does not point to a hardware fault.

## Recover

Auto repair should replace the node by itself. To replace it now, take the instance out of the ASG without lowering desired capacity, so the ASG launches a replacement:

```bash
kubectl get node <node> -o jsonpath='{.spec.providerID}'   # aws:///<az>/<instance-id>
aws autoscaling terminate-instance-in-auto-scaling-group \
  --instance-id <instance-id> --no-should-decrement-desired-capacity
```

The old instance waits in `Terminating:Wait` for up to 30 minutes: the ASG's `Terminate-LC-Hook` has a 1800 s heartbeat, and a dead kubelet can't drain. Until it is gone, its IPs stay allocated.

**Watch for subnet exhaustion.** The nodegroup's subnets are small and shared with the rest of the account (ReCiter nodes, Lambdas, load balancers, RDS, VPC endpoints). On 2026-10-08 the us-east-1a subnet `subnet-081ea573064846f3b` (10.46.134.64/26) had **0** free addresses, so the replacement launch failed with `InsufficientFreeAddressesInSubnet`. A t3.medium node holds ~15 IPs through the VPC CNI warm pool. The ASG retried 1a for ~15 minutes before launching into a 1b subnet that still had addresses. Check:

```bash
aws autoscaling describe-scaling-activities --auto-scaling-group-name <asg> --max-items 3
aws ec2 describe-subnets --subnet-ids <asg subnets> \
  --query 'Subnets[].[SubnetId,AvailabilityZone,AvailableIpAddressCount]' --output text
```

Then confirm both nodes are `Ready` and no pod in `cviche-dev` is `Pending` or `Terminating`. Restart any run that failed while the node was down. Runs resume from their last completed stage.

## Measure worker memory

The workers run on cgroup v2. The kernel keeps each container's exact high-water mark in `memory.peak` from the moment the container starts, so no sampler is needed. Read it after a batch, before anything restarts the pods:

```bash
for p in $(kubectl get pods -n cviche-dev -o name | grep worker); do
  echo "$p $(kubectl exec -n cviche-dev $p -- sh -c \
    'echo peak=$(cat /sys/fs/cgroup/memory.peak) now=$(cat /sys/fs/cgroup/memory.current) $(grep ^oom_kill /sys/fs/cgroup/memory.events)')"
done
kubectl get pods -n cviche-dev -o custom-columns=N:.metadata.name,RESTARTS:.status.containerStatuses[0].restartCount,LAST:.status.containerStatuses[0].lastState.terminated.reason
```

Values are in bytes. The limit is 2147483648 (2 GiB). A non-zero `oom_kill`, or `OOMKilled` under LAST, means the limit was hit.

First reading, 2026-10-08: an idle worker that hasn't run anything peaks at ~0.2 GiB. The worker that ran one docx CV (BCTOGR) peaked at 1.37 GiB and still held 1.34 GiB after the run finished. That memory is anonymous memory in the `python -m app.worker` process, so a worker keeps about 1.1 GiB per run it has done. Six such workers need more memory than two t3.medium nodes have.

## Incident log

- **2026-10-08, batch YUYVIG.** Node `ip-10-46-134-78` stopped reporting at ~03:09 UTC, mid-batch, and stayed down ~9.5 h until it was terminated by hand. Two runs lost (BCTOGR, SZHPJW). Auto repair was enabled the same day. The manual termination at 12:41 UTC hit `InsufficientFreeAddressesInSubnet` in us-east-1a six times. The ASG then launched the replacement into a us-east-1b subnet, and all workers were Running by 12:57. Both nodes are now in us-east-1b. Details on #82.
