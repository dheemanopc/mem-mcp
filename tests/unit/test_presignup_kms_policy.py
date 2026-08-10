"""Regression guard for the PreSignUp Lambda's kms:Decrypt grant.

The trigger failed closed in prod because its IAM policy named the KMS key by
*alias* ARN (``arn:aws:kms:...:alias/mem-mcp``). IAM does not resolve KMS aliases
in a policy ``Resource`` element, so the statement matched nothing, every
``GetParameter(WithDecryption=true)`` returned AccessDenied, and every signup was
denied regardless of invite status.

cfn-lint cannot catch this: an alias ARN is syntactically valid: it is only
semantically inert. Hence a test.

Known outstanding instance of the same bug, tracked separately: the EC2 instance
role's ``EncryptDecryptMemMcpKey`` statement in ``060-compute.yaml`` has the same
alias-ARN Resource. It is currently latent (the app reads its secrets from
``/etc/mem-mcp/env``, written once at bootstrap, rather than calling SSM at
runtime), and fixing it needs the real key ARN threaded through the parent stack
rather than this template's encryption-context trick, so it is deliberately not
covered here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
import yaml

TEMPLATE = (
    Path(__file__).resolve().parents[2] / "infra" / "cfn" / "nested" / "050-lambda-presignup.yaml"
)

PARAMETER_ARN = (
    "arn:aws:ssm:${AWS::Region}:${AWS::AccountId}:parameter/mem-mcp/internal/lambda/secret"
)


class _CfnLoader(yaml.SafeLoader):  # type: ignore[misc]
    """SafeLoader that tolerates CloudFormation short-form intrinsics (!Sub, !Ref)."""


def _intrinsic(loader: yaml.Loader, tag_suffix: str, node: yaml.Node) -> Any:
    """Render ``!Sub 'x'`` as ``{'Fn::Sub': 'x'}`` so assertions can read the string."""
    key = tag_suffix if tag_suffix.startswith("Fn::") else f"Fn::{tag_suffix}"
    if isinstance(node, yaml.ScalarNode):
        value: Any = loader.construct_scalar(node)
    elif isinstance(node, yaml.SequenceNode):
        value = loader.construct_sequence(node, deep=True)
    else:
        value = loader.construct_mapping(node, deep=True)
    return {"Ref": value} if tag_suffix == "Ref" else {key: value}


_CfnLoader.add_multi_constructor("!", _intrinsic)


@pytest.fixture(scope="module")
def template() -> dict[str, Any]:
    return cast(dict[str, Any], yaml.load(TEMPLATE.read_text(), Loader=_CfnLoader))


@pytest.fixture(scope="module")
def kms_statement(template: dict[str, Any]) -> dict[str, Any]:
    """The single kms:Decrypt statement on the PreSignUp function's role."""
    policies = template["Resources"]["PreSignUpFn"]["Properties"]["Policies"]
    statements = [s for policy in policies for s in policy["Statement"]]
    kms = [s for s in statements if "kms" in str(s["Action"])]
    assert len(kms) == 1, f"expected exactly one kms statement, got {len(kms)}"
    return cast(dict[str, Any], kms[0])


def _resource_string(statement: dict[str, Any], template: dict[str, Any]) -> str:
    """Render the Resource, substituting template parameter defaults.

    Substitution matters: the bug was ``${KmsKeyAlias}``, which only reveals
    itself as an alias ARN once the parameter's default is filled in. Asserting
    on the raw Fn::Sub string would miss it.
    """
    resource = statement["Resource"]
    assert (
        isinstance(resource, dict) and "Fn::Sub" in resource
    ), f"expected an Fn::Sub resource, got {resource!r}"
    rendered = str(resource["Fn::Sub"])
    for name, spec in (template.get("Parameters") or {}).items():
        if "Default" in spec:
            rendered = rendered.replace(f"${{{name}}}", str(spec["Default"]))
    return rendered


def test_kms_resource_is_not_an_alias_arn(
    kms_statement: dict[str, Any], template: dict[str, Any]
) -> None:
    """An alias ARN here silently denies every decrypt. This is THE bug."""
    resource = _resource_string(kms_statement, template)
    assert ":alias/" not in resource, (
        "kms:Decrypt Resource names the key by alias ARN; IAM does not resolve "
        f"KMS aliases in a policy Resource, so this matches nothing: {resource}"
    )


def test_kms_resource_targets_a_key_arn(
    kms_statement: dict[str, Any], template: dict[str, Any]
) -> None:
    resource = _resource_string(kms_statement, template)
    assert ":key/" in resource, f"kms:Decrypt Resource must be a key ARN, got {resource}"


def test_kms_decrypt_is_scoped_by_encryption_context(
    kms_statement: dict[str, Any],
) -> None:
    """``key/*`` is only acceptable because this condition pins the ciphertext.

    Without it the role could decrypt anything in the account, including keys
    belonging to unrelated workloads.
    """
    condition = kms_statement["Condition"]["StringEquals"]
    context = condition["kms:EncryptionContext:PARAMETER_ARN"]
    assert context == {
        "Fn::Sub": PARAMETER_ARN
    }, f"encryption-context condition must pin the shared-secret parameter, got {context!r}"


def test_kms_grant_is_decrypt_only(kms_statement: dict[str, Any]) -> None:
    """The trigger only reads the secret; it never writes one."""
    assert kms_statement["Action"] == "kms:Decrypt"
    assert kms_statement["Effect"] == "Allow"


def test_kms_key_alias_parameter_is_not_used_in_an_iam_resource() -> None:
    """Guard the specific regression: re-wiring KmsKeyAlias into a Resource."""
    raw = TEMPLATE.read_text()
    offending = [
        line.strip() for line in raw.splitlines() if "Resource:" in line and "KmsKeyAlias" in line
    ]
    assert not offending, (
        "KmsKeyAlias must not be interpolated into an IAM policy Resource "
        f"(it renders an alias ARN, which matches nothing): {offending}"
    )
