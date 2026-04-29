"""browse/api/embeddings.py — GET /api/embeddings — 2D projection points.

Reuses browse.core.projection.get_projection (same as /api/embeddings/points).
Response shape: EmbeddingProjection.

  {
    "points": [{ x, y, id, title, category }, ...],
    "method": "pca" | "tsne" | ...
  }

Returns 503 if projections not available (no embeddings in DB).
"""

import logging
import os
import sys
import traceback

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

from browse.api._common import json_error, json_ok
from browse.core.projection import get_projection
from browse.core.registry import route

logger = logging.getLogger(__name__)


@route("/api/embeddings", methods=["GET"])
def handle_api_embeddings(db, params, token, nonce) -> tuple:
    try:
        result = get_projection(db)
    except RuntimeError as e:
        return json_error(str(e), "EMBEDDINGS_UNAVAILABLE", 503)
    except Exception:
        traceback.print_exc()
        return json_error("Failed to load embeddings", "INTERNAL_ERROR", 500)
    # get_projection uses PCA; inject the method field required by EmbeddingProjection TS contract
    result.setdefault("method", "pca")
    return json_ok(result)
