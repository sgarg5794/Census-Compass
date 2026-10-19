"""Pinned-model embedding generation (implementation pending).

Build catalog vectors explicitly and reuse them across application restarts.
Generate only query vectors during ordinary retrieval. No index manifest is
planned for this POC. Do not silently select a default embedding model.
"""
