/**
 * View 4 — Deployment topology (EKS, Kustomize prod overlay). The ALB ingress,
 * the frontend/backend Deployments, the HPA, IRSA → Bedrock, and the mounted
 * config/secret/cert material.
 *
 * Source: k8s/base/ingress.yaml · k8s/base/{frontend,backend}/{deployments,services}.yaml ·
 *   k8s/overlays/prod/{kustomization,backend-patch,backend-hpa,sa-patch,ingress-patch}.yaml
 */
import { A } from "../lib.mjs";

const nodes = {
  user: { x: 40, y: 250, w: 180, h: 76, kind: "ext", title: "Browser",
          sub: ["cviche.weill.cornell.edu"] },

  alb: { x: 270, y: 238, w: 230, h: 100, kind: "edge", title: "ALB Ingress",
         sub: ["AWS LB Controller", "HTTP 80 → 443 redirect", "/api → backend · / → frontend"] },

  fe: { x: 580, y: 120, w: 250, h: 92, kind: "net", title: "frontend Deployment",
        sub: ["React build (nginx)", "Service :80 · replicas 1", "req 500m / 512Mi"] },

  be: { x: 580, y: 250, w: 250, h: 120, kind: "app", title: "backend Deployment",
        sub: ["FastAPI · uvicorn :8000", "prod: replicas 2, req/lim 2 vCPU / 2Gi",
              "CVICHE_MAX_CONCURRENT_RUNS=3"] },

  hpa: { x: 580, y: 410, w: 250, h: 70, kind: "app", title: "HorizontalPodAutoscaler",
         sub: ["CPU 70% · min 2 / max 4", "(prod overlay only)"] },

  cfg: { x: 900, y: 120, w: 240, h: 96, kind: "data", title: "Mounted config",
         sub: ["auth_config.yaml (ConfigMap)", "SAML key/crt (Secret)", "db.env + secrets (Secret)"] },

  sa: { x: 900, y: 250, w: 240, h: 78, kind: "aws", title: "ServiceAccount (IRSA)",
        sub: ["cviche-sa", "→ cviche-bedrock-role"] },

  bedrock: { x: 1200, y: 120, w: 220, h: 80, kind: "aws", title: "AWS Bedrock",
             sub: ["Claude Sonnet 4.6 / Haiku 4.5", "via IRSA role"] },
  db: { x: 1200, y: 232, w: 220, h: 64, kind: "data", title: "MariaDB",
        sub: ["DB_HOST / DB_PORT / DB_NAME / DB_USER"] },
  s3: { x: 1200, y: 360, w: 220, h: 64, kind: "data", title: "Amazon S3",
        sub: ["cviche/runs/{id}/…"] },
};

const groups = [
  { x: 560, y: 92, w: 600, h: 430, kind: "app", title: "EKS · namespace cviche-prod" },
  { x: 1180, y: 100, w: 260, h: 360, kind: "data", title: "AWS managed" },
];

const edges = [
  { p0: A(nodes.user, "r"), p1: A(nodes.alb, "l"), color: "maroon", label: "HTTPS" },
  { p0: A(nodes.alb, "r", 0.25), p1: A(nodes.fe, "l"), color: "indigo", label: "/  (frontend)" },
  { p0: A(nodes.alb, "r", 0.75), p1: A(nodes.be, "l"), color: "green", label: "/api · /ws" },
  { p0: A(nodes.hpa, "t"), p1: A(nodes.be, "b"), color: "green", dash: true, label: "scales 2–4" },

  { p0: A(nodes.be, "r", 0.45), p1: A(nodes.sa, "l"), color: "violet", label: "assumes role" },
  { p0: A(nodes.be, "r", 0.12), p1: A(nodes.cfg, "l", 0.7), color: "amber", label: "mounts" },
  { p0: A(nodes.sa, "r"), p1: A(nodes.bedrock, "l", 0.7), color: "violet", label: "Bedrock (IRSA)" },

  // be → db / s3 route through a clear lane BELOW the IRSA ServiceAccount box.
  { p0: A(nodes.be, "r", 0.78), p1: A(nodes.db, "l", 0.6), color: "amber",
    points: [{ x: 865, y: 344 }, { x: 1170, y: 344 }], label: "SQLAlchemy" },
  { p0: A(nodes.be, "r", 0.95), p1: A(nodes.s3, "l"), color: "amber", label: "boto3" },
];

export const spec = { id: "deployment", vb: [1450, 560], groups, nodes, edges };

export const meta = {
  nav: "④ Deployment",
  kicker: "View 4 · deployment topology",
  heading: "Deployment topology (EKS)",
  dot: "#7048e8",
  blurb:
    "Kustomize renders a base + prod overlay into namespace <b>cviche-prod</b> on EKS. An " +
    "<b>ALB Ingress</b> terminates TLS for <code>cviche.weill.cornell.edu</code> and routes " +
    "<code>/api</code> + <code>/ws</code> to the <b>backend</b> Deployment and everything else to the " +
    "<b>frontend</b>. In prod the backend runs <b>2 replicas</b> (2 vCPU / 2Gi each) under an " +
    "<b>HPA</b> (CPU 70%, 2–4 pods); it reaches <b>Bedrock</b> through an <b>IRSA</b> ServiceAccount " +
    "and reads <b>MariaDB</b> + <b>S3</b>. Auth config, SAML certs, and DB secrets are mounted from a " +
    "ConfigMap and Secrets.",
  legend: [
    { fill: "#fbeaea", stroke: "#7d1c1c", label: "Ingress / edge" },
    { fill: "#e7ecff", stroke: "#4263eb", label: "Frontend" },
    { fill: "#e3faf3", stroke: "#0ca678", label: "Backend / autoscale" },
    { fill: "#f0ebff", stroke: "#7048e8", label: "IAM / Bedrock" },
    { fill: "#fff4d6", stroke: "#f08c00", label: "Config / data" },
  ],
  edgeLegend: [
    { color: "maroon", label: "public HTTPS" },
    { color: "indigo", label: "route to frontend" },
    { color: "green", label: "route to backend" },
    { color: "green", dash: true, label: "autoscaling" },
    { color: "violet", label: "IAM-scoped (IRSA) → Bedrock" },
    { color: "amber", label: "mount / data access" },
  ],
  footnote:
    "Replicas&nbsp;> 1 requires <code>CVICHE_REDIS_URL</code> so live progress and cancellation cross pods " +
    "(view ③). Dev overlay differs: namespace <code>cviche-dev</code>, <code>CVICHE_DISPATCH_MODE=queue</code> " +
    "(runs execute on <code>cviche-worker</code> pods, 3 general + 3 flex replicas, not in the backend), " +
    "backend HPA fixed at 2 replicas (150m/512Mi req, 2/2Gi limits).",
  seeAlso: [
    { id: "context", label: "① system context" },
    { id: "runtime", label: "③ what runs in the backend pod" },
  ],
  source:
    "k8s/base/ingress.yaml · k8s/overlays/prod/kustomization.yaml · k8s/overlays/prod/backend-patch.yaml · " +
    "k8s/overlays/prod/backend-hpa.yaml · k8s/overlays/prod/sa-patch.yaml",
};
