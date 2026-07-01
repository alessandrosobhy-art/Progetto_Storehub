
# ui_enrichment.py — helper per adattare i dati alle aspettative del template reviews.html
# Crea i campi: _review_name, _stars, _replyText e normalizza comment

def _normalize_stars(val):
    if val is None:
        return 0
    try:
        v = int(val)
        if 1 <= v <= 5:
            return v
    except Exception:
        pass
    mapping = {
        "ONE":1,"TWO":2,"THREE":3,"FOUR":4,"FIVE":5,
        "one":1,"two":2,"three":3,"four":4,"five":5,
        "1":1,"2":2,"3":3,"4":4,"5":5
    }
    return mapping.get(str(val), 0)

def enrich_reviews_for_template(items):
    for rev in items or []:
        # hidden name per il form
        rev["_review_name"] = rev.get("name") or rev.get("_review_name") or ""
        # stelle per la UI
        rev["_stars"] = _normalize_stars(rev.get("starRating"))
        # testo risposta precompilato
        rep = (rev.get("reviewReply") or
               rev.get("reply") or
               rev.get("ownerReply") or
               rev.get("review_reply"))
        reply_text = ""
        if isinstance(rep, dict):
            reply_text = rep.get("comment") or rep.get("text") or ""
        elif isinstance(rev.get("replyText"), str):
            reply_text = rev["replyText"] or ""
        rev["_replyText"] = reply_text
        # mai None nel commento utente
        if rev.get("comment") is None:
            rev["comment"] = ""
    return items
