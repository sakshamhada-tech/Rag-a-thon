"""Shared pytest configuration.

Hermetic by default: tests never fetch the fastembed model over the network.
Set FIXORA_EMBEDDER=fastembed before pytest to opt into a real neural-embedder
run (downloads ~30 MB on first use).
"""
import os

os.environ.setdefault("FIXORA_EMBEDDER", "hash")
