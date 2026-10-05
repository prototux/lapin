"""Long-term memory: facts the user asked to remember (opt-in by nature)."""

from . import tool
from ..memory import rank_facts


@tool("Remember a fact or preference for later conversations (only when the user asks, or "
      "clearly states a lasting preference).", {"fact": ("string", "the fact, self-contained, e.g. "
                                                          "'Anna is allergic to peanuts'")}, ["fact"],
      roles=("admin", "adult", "child"))
def remember(ctx, fact):
    fid = ctx.app.store.add_fact(fact.strip(), ctx.user if ctx.private_memory else "")
    return {"ok": True, "id": fid}


@tool("Look up remembered facts.", {"query": ("string", "what to look for")}, ["query"])
def recall(ctx, query):
    facts = rank_facts(ctx.app.store.facts(ctx.user), query, limit=8)
    return {"facts": [{"id": f["id"], "text": f["text"]} for f in facts]}


@tool("Forget remembered facts matching a description.", {"query": ("string", "which fact(s)"),
                                                          "id": ("integer", "exact id from recall")},
      roles=("admin", "adult"))
def forget(ctx, query="", id=None):
    facts = ctx.app.store.facts(ctx.user)
    if id is not None:
        hits = [f for f in facts if f["id"] == int(id)]
    else:
        hits = rank_facts(facts, query, limit=3, min_score=0.5)
    for f in hits:
        ctx.app.store.delete_fact(f["id"])
    return {"forgotten": [f["text"] for f in hits]}


EXAMPLES = {"en": ["Remember that the wifi password is on the fridge", "Remember that Anna is allergic to peanuts", "What did I tell you about the wifi?", "Forget what I said about the wifi"],
            "fr": ["Souviens-toi que le code du portail est 1234", "Retiens que Marie est allergique aux arachides", "Qu'est-ce que je t'ai dit sur le portail ?", "Oublie ce que je t'ai dit sur le portail"]}
