# AWS Bedrock Data-Protection Reference

The strongest AWS references are:

- [Amazon Bedrock FAQs — Security](https://aws.amazon.com/bedrock/faqs/#Security): AWS states, “Users’ inputs and model outputs are not shared with any model providers.” It also says neither AWS nor third-party providers use Bedrock inputs or outputs to train models.
- [Amazon Bedrock — Data protection](https://docs.aws.amazon.com/bedrock/latest/userguide/data-protection.html): AWS explains that each provider’s model is deployed in a Model Deployment Account “owned and operated by the Amazon Bedrock service team.” Providers have no access to those accounts, Bedrock logs, prompts, or completions.

## Suggested wording

> AWS states that Amazon Bedrock does not share customer inputs or model outputs with model providers—including Anthropic—and that neither AWS nor third-party providers use them to train models. Provider models are deployed in AWS-owned and AWS-operated Model Deployment Accounts that model providers cannot access. See the [Amazon Bedrock FAQs](https://aws.amazon.com/bedrock/faqs/#Security) and AWS’s [Bedrock data-protection documentation](https://docs.aws.amazon.com/bedrock/latest/userguide/data-protection.html).

For a contractual or compliance assertion, cite the applicable WCM–AWS agreement and incorporated AWS terms; the repository is not the source of that commitment. Note that AWS may retain inputs and outputs for certain models for abuse detection, but says this remains within the AWS boundary and is not provider sharing.
