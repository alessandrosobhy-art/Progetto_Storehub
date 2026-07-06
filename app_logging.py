"""Logging di supporto per eccezioni intenzionalmente inghiottite.

I blocchi ``except Exception: pass`` storici del progetto nascondevano ogni
errore, rendendo i bug visibili solo come "dati mancanti". ``log_swallowed``
li rende osservabili senza cambiarne il comportamento: di default logga a
DEBUG (invisibile con il livello INFO di produzione); impostando la variabile
d'ambiente ``SWALLOWED_LOG_LEVEL=WARNING`` (o INFO/ERROR) le eccezioni
inghiottite compaiono nei log con stack trace, utile quando si diagnostica
un dato che sparisce in silenzio.
"""
from __future__ import annotations

import logging
import os

_logger = logging.getLogger("storehub.swallowed")
_LEVEL = getattr(
    logging,
    (os.getenv("SWALLOWED_LOG_LEVEL") or "DEBUG").strip().upper(),
    logging.DEBUG,
)


def log_swallowed(where: str) -> None:
    """Registra l'eccezione corrente; da chiamare dentro un blocco except."""
    try:
        _logger.log(_LEVEL, "eccezione ignorata in %s", where, exc_info=True)
    except Exception:
        # Il logging non deve mai far fallire il chiamante.
        pass
