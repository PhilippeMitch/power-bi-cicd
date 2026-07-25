"""Deploy Fabric/Power BI workspace items using fabric-cicd.

Called from GitHub Actions with a service principal (client credentials
flow). Credentials and the target workspace ID are passed in as
environment variables / CLI arguments that come from GitHub Environment
secrets, so the same script works for DEV, VAL and PROD.
"""

import argparse
import os
import sys

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

    print(f"Publishing items from '{args.repo_dir}' to workspace {args.workspace_id} ({args.environment})")
    publish_all_items(target_workspace)

    if args.unpublish_orphans:
        print("Removing orphaned items from the target workspace")
        unpublish_all_orphan_items(target_workspace)

    print("Deployment finished successfully.")


if __name__ == "__main__":
    main()
