"""Shared asynchronous Prisma client for the FastAPI application."""

from prisma import Prisma

prisma = Prisma()


async def connect_prisma() -> None:
    if not prisma.is_connected():
        await prisma.connect()


async def disconnect_prisma() -> None:
    if prisma.is_connected():
        await prisma.disconnect()
