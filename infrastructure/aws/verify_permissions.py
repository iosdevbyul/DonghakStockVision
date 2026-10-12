"""Read-only installed-policy verification; never invoke destructive S3 APIs.

Run with the project virtualenv. Output contains no credentials. Custom Policy
Simulator evaluates the exact document read back from IAM; actual allowed access
is checked separately through the dedicated runtime role.
"""

import json
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

ROLE = "DonghakCustodyWriterRole"
POLICY = "DonghakCustodyEvidenceAccess"
CONFIG = Config(connect_timeout=5, read_timeout=10, retries={"total_max_attempts": 2})


def verify() -> dict:
    admin = boto3.Session(profile_name="default", region_name="ap-southeast-2")
    iam = admin.client("iam", config=CONFIG)
    assert iam.list_role_policies(RoleName=ROLE)["PolicyNames"] == [POLICY]
    assert iam.list_attached_role_policies(RoleName=ROLE)["AttachedPolicies"] == []
    installed = iam.get_role_policy(RoleName=ROLE, PolicyName=POLICY)["PolicyDocument"]
    expected = json.loads(
        Path(__file__).with_name("donghak-custody-writer-policy.json").read_text()
    )
    assert installed == expected
    actions = [
        "s3:PutObject",
        "s3:GetObject",
        "s3:GetObjectVersion",
        "s3:GetObjectRetention",
        "s3:DeleteObject",
        "s3:DeleteObjectVersion",
        "s3:BypassGovernanceRetention",
        "s3:PutObjectRetention",
        "s3:PutObjectLegalHold",
        "s3:PutObjectAcl",
    ]
    resources = [
        "arn:aws:s3:::donghak-custody-dev/evidence/check.json",
        "arn:aws:s3:::donghak-custody-dev/dev-tests/check.json",
        "arn:aws:s3:::donghak-custody-dev/unapproved/check.json",
        "arn:aws:s3:::donghak-other-bucket/dev-tests/check.json",
    ]
    results = []
    for evaluation in iam.simulate_custom_policy(
        PolicyInputList=[json.dumps(installed)], ActionNames=actions, ResourceArns=resources
    )["EvaluationResults"]:
        for resource in evaluation["ResourceSpecificResults"]:
            decision = resource["EvalResourceDecision"]
            action = evaluation["EvalActionName"]
            arn = resource["EvalResourceName"]
            expected_decision = (
                "allowed" if action in actions[:4] and arn in resources[:2] else "implicitDeny"
            )
            assert decision == expected_decision, (action, arn, decision)
            results.append({"action": action, "resource": arn, "decision": decision})
    for prefix in ("evidence/check/", "dev-tests/check/", "unapproved/", ""):
        decision = iam.simulate_custom_policy(
            PolicyInputList=[json.dumps(installed)],
            ActionNames=["s3:ListBucketVersions"],
            ResourceArns=["arn:aws:s3:::donghak-custody-dev"],
            ContextEntries=[
                {
                    "ContextKeyName": "s3:prefix",
                    "ContextKeyType": "string",
                    "ContextKeyValues": [prefix],
                }
            ],
        )["EvaluationResults"][0]["EvalDecision"]
        assert decision == (
            "allowed" if prefix.startswith(("evidence/", "dev-tests/")) else "implicitDeny"
        )
        results.append({"action": "s3:ListBucketVersions", "prefix": prefix, "decision": decision})
    for action, arn in (
        ("s3:PutBucketVersioning", "arn:aws:s3:::donghak-custody-dev"),
        ("s3:PutBucketObjectLockConfiguration", "arn:aws:s3:::donghak-custody-dev"),
        ("s3:PutBucketPolicy", "arn:aws:s3:::donghak-custody-dev"),
        ("iam:PutRolePolicy", "arn:aws:iam::101180464666:role/DonghakCustodyWriterRole"),
    ):
        decision = iam.simulate_custom_policy(
            PolicyInputList=[json.dumps(installed)],
            ActionNames=[action],
            ResourceArns=[arn],
        )["EvaluationResults"][0]["EvalDecision"]
        assert decision == "implicitDeny"
        results.append({"action": action, "resource": arn, "decision": decision})
    runtime = boto3.Session(profile_name="donghak-custody", region_name="ap-southeast-2")
    identity = runtime.client("sts", config=CONFIG).get_caller_identity()["Arn"]
    assert identity.startswith("arn:aws:sts::101180464666:assumed-role/DonghakCustodyWriterRole/")
    runtime.client("s3", config=CONFIG).list_object_versions(
        Bucket="donghak-custody-dev", Prefix="dev-tests/permission-read-only-check/", MaxKeys=1
    )
    negative_runtime = []
    for prefix in ("unapproved/permission-check/", ""):
        try:
            runtime.client("s3", config=CONFIG).list_object_versions(
                Bucket="donghak-custody-dev", Prefix=prefix, MaxKeys=1
            )
        except ClientError as error:
            assert error.response["Error"]["Code"] == "AccessDenied"
            negative_runtime.append(
                {"operation": "ListObjectVersions", "prefix": prefix, "errorCode": "AccessDenied"}
            )
        else:
            raise AssertionError("unapproved prefix unexpectedly accessible")
    return {
        "runtimeDeniedPrefixListing": negative_runtime,
        "roleArn": identity,
        "inlinePolicies": [POLICY],
        "attachedManagedPolicies": [],
        "installedPolicyMatchesReviewedFile": True,
        "simulator": "SimulateCustomPolicy on exact IAM-readback policy",
        "principalSimulatorLimitation": "Organizations evaluation denied even actually allowed operations",
        "runtimeAllowedVersionListingVerified": True,
        "evaluations": results,
    }


if __name__ == "__main__":
    try:
        report = verify()
    except Exception:
        raise SystemExit(
            "Read-only AWS permission verification failed; no credential details emitted"
        ) from None
    print(json.dumps(report, indent=2))
