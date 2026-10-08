"""Trusted operator CLI for provisioning and revoking frontend credentials."""

import argparse
import asyncio
from datetime import datetime, timezone
from urllib.parse import urlparse

from dotenv import load_dotenv

from api.v1.auth.security import key_hash, new_client_key
from helpers.prisma import connect_prisma, disconnect_prisma, prisma


def validate_origin(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise argparse.ArgumentTypeError("Origin must be scheme://host[:port] with no path")
    return value.rstrip("/")


async def run(args: argparse.Namespace) -> None:
    await connect_prisma()
    try:
        if args.command == "create":
            key = new_client_key()
            client = await prisma.apiclient.create(data={
                "name": args.name,
                "keyPrefix": key[:12],
                "keyHash": key_hash(key),
                "allowedOrigins": list(dict.fromkeys(args.origin)),
            })
            print(f"Created {client.name} ({client.id}). Store this key in the frontend server's secret manager; it will not be shown again:\n{key}")
        elif args.command == "list":
            clients = await prisma.apiclient.find_many(order={"createdAt": "asc"})
            for client in clients:
                state = "revoked" if client.revokedAt else "active"
                print(f"{client.name}\t{client.id}\t{state}\t{client.keyPrefix}\tlast_used={client.lastUsedAt or '-'}\torigins={','.join(client.allowedOrigins)}")
        elif args.command == "usage":
            rows = await prisma.query_raw(
                'SELECT c."name", c."revoked_at", count(a."id")::int AS "requests_24h", '
                'max(a."created_at") AS "last_request" FROM "public"."api_clients" c '
                'LEFT JOIN "public"."api_request_audit" a ON a."client_id" = c."id" '
                'AND a."created_at" >= now() - interval \'24 hours\' '
                'GROUP BY c."id" ORDER BY "requests_24h" DESC, c."name"'
            )
            for row in rows:
                state = "revoked" if row["revoked_at"] else "active"
                print(f'{row["name"]}\t{state}\trequests_24h={row["requests_24h"]}\tlast_request={row["last_request"] or "-"}')
        else:
            client = await prisma.apiclient.find_unique(where={"name": args.name})
            if client is None:
                raise SystemExit(f"Unknown frontend: {args.name}")
            now = datetime.now(timezone.utc)
            await prisma.apisession.update_many(where={"clientId": client.id, "revokedAt": None}, data={"revokedAt": now})
            if args.command == "revoke":
                await prisma.apiclient.update(where={"id": client.id}, data={"revokedAt": now})
                print(f"Revoked {client.name} and its sessions")
            elif args.command == "rotate":
                key = new_client_key()
                await prisma.apiclient.update(where={"id": client.id}, data={
                    "keyPrefix": key[:12], "keyHash": key_hash(key), "revokedAt": None,
                })
                print(f"Rotated {client.name}; all old sessions are revoked. Store this new key in the frontend server's secret manager:\n{key}")
    finally:
        await disconnect_prisma()


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Manage frontend API keys")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("name")
    create.add_argument("--origin", type=validate_origin, action="append", default=[])
    commands.add_parser("list")
    commands.add_parser("usage")
    for command in ("revoke", "rotate"):
        commands.add_parser(command).add_argument("name")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
