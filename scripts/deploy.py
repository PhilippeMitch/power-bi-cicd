"""Deploy Fabric/Power BI workspace items using fabric-cicd.

Called from GitHub Actions with a service principal (client credentials
flow). Credentials and the target workspace ID are passed in as
environment variables / CLI arguments that come from GitHub Environment
secrets, so the same script works for DEV, VAL and PROD.
"""

import argparse
import glob
import os
import sys

import requests
from azure.identity import ClientSecretCredential
from fabric_cicd import FabricWorkspace, publish_all_items, unpublish_all_orphan_items


def get_credential() -> ClientSecretCredential:
    try:
        tenant_id = os.environ["AZURE_TENANT_ID"]
        client_id = os.environ["AZURE_CLIENT_ID"]
        client_secret = os.environ["AZURE_CLIENT_SECRET"]
    except KeyError as exc:
        sys.exit(f"Missing required environment variable: {exc}")

    return ClientSecretCredential(
        tenant_id=tenant_id,
        client_id=client_id,
        client_secret=client_secret,
    )


def take_over_semantic_models(workspace_id: str, repo_dir: str, credential: ClientSecretCredential) -> None:
    """Take ownership of any pre-existing semantic models before fabric-cicd binds connections.

    fabric-cicd's semantic_model_binding step calls the Fabric bindConnection API, which
    only succeeds if the caller *owns* the semantic model (microsoft/fabric-cicd#824, open
    as of 2026-08 - fabric-cicd does not take ownership itself). A model first published by
    a human (e.g. via Fabric Git sync) keeps that human as owner even after this service
    principal republishes its content, so binding fails with "you are not the owner". This
    call is idempotent, so running it on every deploy is safe.
    """
    model_names = {
        os.path.basename(path)[: -len(".SemanticModel")]
        for path in glob.glob(os.path.join(repo_dir, "*.SemanticModel"))
    }
    if not model_names:
        return

    fabric_token = credential.get_token("https://api.fabric.microsoft.com/.default").token
    list_resp = requests.get(
        f"https://api.fabric.microsoft.com/v1/workspaces/{workspace_id}/semanticModels",
        headers={"Authorization": f"Bearer {fabric_token}"},
        timeout=30,
    )
    list_resp.raise_for_status()
    models_by_name = {model["displayName"]: model["id"] for model in list_resp.json().get("value", [])}

    powerbi_token = credential.get_token("https://analysis.windows.net/powerbi/api/.default").token
    for name in sorted(model_names):
        model_id = models_by_name.get(name)
        if not model_id:
            # Doesn't exist in the workspace yet - fabric-cicd will create it and this
            # service principal will own it automatically, so no takeover is needed.
            continue

        takeover_resp = requests.post(
            f"https://api.powerbi.com/v1.0/myorg/groups/{workspace_id}/datasets/{model_id}/Default.TakeOver",
            headers={"Authorization": f"Bearer {powerbi_token}"},
            timeout=30,
        )
        if takeover_resp.ok:
            print(f"Took ownership of semantic model '{name}' ({model_id})")
        else:
            print(
                f"::warning::Failed to take ownership of semantic model '{name}': "
                f"{takeover_resp.status_code} {takeover_resp.text[:200]}"
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Deploy Fabric items with fabric-cicd")
    parser.add_argument("--workspace-id", required=True, help="Target Fabric workspace ID (GUID)")
    parser.add_argument(
        "--environment",
        required=True,
        choices=["DEV", "VAL", "PROD"],
        help="Deployment environment; used to resolve values in config/parameter.yml",
    )
    parser.add_argument(
        "--repo-dir",
        required=True,
        help="Folder containing the exported Fabric workspace items (e.g. 'workspace')",
    )
    parser.add_argument(
        "--item-types",
        nargs="+",
        default=["Notebook", "DataPipeline", "Environment", "SemanticModel", "Report"],
        help="Fabric item types to include in the deployment",
    )
    parser.add_argument(
        "--unpublish-orphans",
        action="store_true",
        help="Remove items from the target workspace that no longer exist in the repo",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    credential = get_credential()

    target_workspace = FabricWorkspace(
        workspace_id=args.workspace_id,
        repository_directory=args.repo_dir,
        item_type_in_scope=args.item_types,
        environment=args.environment,
        token_credential=credential,
    )

    take_over_semantic_models(args.workspace_id, args.repo_dir, credential)

    print(f"Publishing items from '{args.repo_dir}' to workspace {args.workspace_id} ({args.environment})")
    publish_all_items(target_workspace)

    if args.unpublish_orphans:
        print("Removing orphaned items from the target workspace")
        unpublish_all_orphan_items(target_workspace)

    print("Deployment finished successfully.")


if __name__ == "__main__":
    main()
