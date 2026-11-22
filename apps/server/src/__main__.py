"""Command line entry point: ``xavier <command>``."""

import argparse
import asyncio
import getpass
import json
import os
import sys
from pathlib import Path

import uvicorn
import yaml

from src.api.app import create_app
from src.auth.service import Accounts
from src.bench import run_bench
from src.config import Settings, load_settings
from src.db.engine import create_engine, create_sessions, migrate
from src.errors import XavierError
from src.logs import configure_logging
from src.orchestration.worker import run_worker
from src.storage.blobs import ensure_buckets, open_s3_client
from src.telemetry import configure_telemetry


def settings_schema() -> str:
    """Return the JSON Schema of ``settings.yaml`` rendered as YAML."""
    return yaml.safe_dump(Settings.model_json_schema(), sort_keys=False, allow_unicode=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="xavier")
    parser.add_argument(
        "--settings",
        type=Path,
        default=Path(os.environ.get("XAVIER_SETTINGS_FILE", "settings.yaml")),
        help="settings file (default: $XAVIER_SETTINGS_FILE or settings.yaml)",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("api", help="serve the REST API")
    commands.add_parser("worker", help="run the Temporal worker and the agent gateway")
    commands.add_parser("migrate", help="upgrade the database schema")
    commands.add_parser("init-storage", help="create buckets and lifecycle rules")
    commands.add_parser("config-schema", help="print the settings JSON Schema as YAML")
    commands.add_parser("openapi", help="print the OpenAPI document of the REST API")
    bench = commands.add_parser(
        "bench", help="measure Compute, Execute and reference chats on this host"
    )
    bench.add_argument("--output", type=Path, required=True, help="Markdown report to write")
    bench.add_argument("--iterations", type=int, default=20, help="samples per action")
    users = commands.add_parser("users", help="manage the users who sign in").add_subparsers(
        dest="action", required=True
    )
    create = users.add_parser("create", help="create a user")
    create.add_argument("username")
    create.add_argument("--name", default="", help="display name (default: the username)")
    users.add_parser("list", help="print username, name and creation time of every user")
    set_password = users.add_parser(
        "set-password", help="replace the password and sign out every session"
    )
    set_password.add_argument("username")
    delete = users.add_parser("delete", help="delete a user and revoke their sessions and keys")
    delete.add_argument("username")
    for command in (create, set_password):
        command.add_argument(
            "--password-stdin", action="store_true", help="read the password from stdin"
        )
    return parser


def _password(*, from_stdin: bool) -> str:
    if from_stdin:
        return sys.stdin.readline().rstrip("\n")
    password = getpass.getpass("Password: ")
    if getpass.getpass("Repeat password: ") != password:
        raise SystemExit("the passwords do not match")
    return password


async def _users(settings: Settings, args: argparse.Namespace) -> None:
    password = (
        _password(from_stdin=args.password_stdin)
        if args.action in {"create", "set-password"}
        else ""
    )
    engine = create_engine(settings.database)
    try:
        async with create_sessions(engine).begin() as session:
            accounts = Accounts(settings.auth, session)
            match args.action:
                case "create":
                    user = await accounts.create_user(args.username, password, name=args.name)
                    sys.stdout.write(f"created {user.username}\n")
                case "list":
                    for user in await accounts.users():
                        sys.stdout.write(
                            f"{user.username}\t{user.name}\t{user.created_at.isoformat()}\n"
                        )
                case "set-password":
                    user = await accounts.find_user(args.username)
                    await accounts.set_password(user.id, password)
                case "delete":
                    user = await accounts.find_user(args.username)
                    await accounts.delete_user(user.id)
    except XavierError as error:
        raise SystemExit(error.detail) from error
    finally:
        await engine.dispose()


async def _init_storage(settings: Settings) -> None:
    async with open_s3_client(settings.s3) as client:
        await ensure_buckets(client, settings.s3)


def main(argv: list[str] | None = None) -> None:
    """Run a Xavier command."""
    args = _parser().parse_args(argv)
    if args.command == "config-schema":
        sys.stdout.write(settings_schema())
        return

    settings = load_settings(args.settings)
    configure_logging(settings.logging, settings.secret_values())
    match args.command:
        case "api":
            configure_telemetry(settings.telemetry, "api")
            uvicorn.run(
                create_app(settings),
                host=settings.api.host,
                port=settings.api.port,
                log_config=None,
                server_header=False,
                # The screen stream is already compressed RFB; deflating it again costs CPU.
                ws_per_message_deflate=False,
                proxy_headers=bool(settings.api.forwarded_allow_ips),
                forwarded_allow_ips=settings.api.forwarded_allow_ips,
            )
        case "worker":
            configure_telemetry(settings.telemetry, "worker")
            asyncio.run(run_worker(settings))
        case "migrate":
            migrate(settings.database)
        case "openapi":
            sys.stdout.write(json.dumps(create_app(settings).openapi(), indent=2) + "\n")
        case "init-storage":
            asyncio.run(_init_storage(settings))
        case "bench":
            args.output.write_text(asyncio.run(run_bench(settings, args.iterations)))
        case "users":
            asyncio.run(_users(settings, args))


if __name__ == "__main__":
    main()
