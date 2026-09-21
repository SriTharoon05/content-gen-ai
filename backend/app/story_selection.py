"""Story-only boundary: two ideation routes, grounding, permanent uniqueness, five-field output."""
import logging
import random
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field
from sqlalchemy import select, text

from .config import boot
from .db import session_scope
from .llm import generate_model
from .models import KnowledgeFrontier, VerificationBacklog
from .schemas import UniqueConceptSet

log = logging.getLogger("story_selection")


class Topic(BaseModel):
    friendly_title: str
    likely_academic_term: str


class Topics(BaseModel):
    topics: list[Topic] = Field(min_length=5, max_length=5)


class SearchProvider:
    def search(self, query: str) -> dict | None:
        raise NotImplementedError


class SerpApiProvider(SearchProvider):
    def search(self, query: str) -> dict | None:
        if not boot().serpapi_key:
            raise RuntimeError("SERPAPI_KEY is required for verified topic-tree channels")
        try:
            logging.getLogger("httpx").setLevel(logging.WARNING)
            with httpx.Client(timeout=20) as client:
                response = client.get("https://serpapi.com/search", params={"q": query, "api_key": boot().serpapi_key, "engine": "google"})
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError):
            return None


class WikimediaSearchProvider(SearchProvider):
    """No-key grounding fallback using the Foundation's own article search."""
    def search(self, query: str) -> dict | None:
        import re
        try:
            r = httpx.get("https://en.wikipedia.org/w/api.php", params={"action": "query", "list": "search",
                "srsearch": query, "srlimit": 5, "format": "json"},
                headers={"User-Agent": boot().wikimedia_user_agent}, timeout=20)
            r.raise_for_status()
            return {"organic_results": [{"snippet": re.sub('<[^>]+>', '', item.get('snippet', '')),
                "link": f"https://en.wikipedia.org/?curid={item['pageid']}"}
                for item in r.json().get('query', {}).get('search', [])]}
        except (httpx.HTTPError, ValueError):
            return None


def search_provider():
    return SerpApiProvider() if boot().serpapi_key else WikimediaSearchProvider()


def score_subtopic(results, min_independent_domains=2):
    valid = [r for r in results if len(r.get("snippet", "")) > 20]
    domains = {urlparse(r.get("link", "")).hostname or "" for r in valid}
    reputable = ("wikipedia.org", "nature.com", "arxiv.org", "sciencedirect.com", "ncbi.nlm.nih.gov")
    if any(any(d == r or d.endswith("." + r) for r in reputable) or d.endswith((".edu", ".gov", ".ac.uk")) for d in domains):
        return True
    # Subdomains of the same site do not count as independent evidence.
    roots = {".".join(d.split(".")[-2:]) for d in domains if d}
    return len(roots) >= min_independent_domains


def verify_and_score(query, search_client):
    for attempt in range(2):
        data = search_client.search(query)
        if data and "organic_results" in data:
            return score_subtopic(data["organic_results"])
        if attempt == 0:
            time.sleep(2)
    return False


def fetch_wikimedia_daily_seed():
    today = datetime.now(timezone.utc)
    url = f"https://en.wikipedia.org/api/rest_v1/feed/onthisday/events/{today.month:02d}/{today.day:02d}"
    response = httpx.get(url, headers={"User-Agent": boot().wikimedia_user_agent, "Accept": "application/json"}, timeout=30, follow_redirects=True)
    if response.status_code in (403, 429, 502, 503):
        time.sleep(2)
        response = httpx.get(url, headers={"User-Agent": boot().wikimedia_user_agent, "Accept": "application/json"}, timeout=30, follow_redirects=True)
    response.raise_for_status()
    events = response.json().get("events", [])
    if not events:
        raise RuntimeError("Wikimedia returned no verified event seeds")
    # Several source-backed events allow choosing one appropriate to the channel.
    chosen = random.sample(events, min(8, len(events)))
    return "\n".join(f"In {e['year']}: {e['text']} Sources: " + ", ".join(p.get("content_urls", {}).get("desktop", {}).get("page", "") for p in e.get("pages", [])[:2]) for e in chosen)


def source_evidence(entity: str) -> str:
    """Downstream research, independent of the selector's five-field output contract."""
    import re
    blocks = []
    with httpx.Client(timeout=30, headers={"User-Agent": boot().wikimedia_user_agent}) as client:
        for term in entity.split(" and ")[:2]:
            r = client.get("https://en.wikipedia.org/w/api.php", params={"action":"query", "list":"search",
                "srsearch":term, "srlimit":1, "format":"json"})
            r.raise_for_status()
            hits = r.json().get("query",{}).get("search",[])
            if not hits:
                continue
            page = hits[0]
            r = client.get("https://en.wikipedia.org/w/api.php", params={"action":"query", "pageids":page["pageid"],
                "prop":"extracts", "explaintext":1, "exchars":10000, "format":"json"})
            r.raise_for_status()
            data = r.json().get("query",{}).get("pages",{}).get(str(page["pageid"]),{})
            extract = re.sub(r"\n{3,}", "\n\n", data.get("extract", ""))
            if extract:
                blocks.append(f"SOURCE https://en.wikipedia.org/?curid={page['pageid']} ({page['title']})\n{extract}")
    if not blocks:
        raise RuntimeError("No supporting source article available for the selected real story")
    return "\n\n".join(blocks)


def _insert_topics(channel_slug, topics, parent=None, client=None):
    if parent and parent.depth_level >= 3:
        return
    client = client or search_provider()
    for topic in topics:
        verified = verify_and_score('"' + topic.likely_academic_term + '"', client)
        with session_scope() as session:
            duplicate = session.scalar(select(KnowledgeFrontier.id).where(KnowledgeFrontier.channel_slug == channel_slug,
                KnowledgeFrontier.academic_term == topic.likely_academic_term).limit(1))
            if duplicate:
                continue
            if verified:
                session.add(KnowledgeFrontier(channel_slug=channel_slug, parent_id=parent.id if parent else None,
                    topic_name=topic.friendly_title, academic_term=topic.likely_academic_term,
                    depth_level=parent.depth_level + 1 if parent else 0))
            else:
                session.add(VerificationBacklog(channel_slug=channel_slug, parent_id=parent.id if parent else None,
                    proposed_term=topic.likely_academic_term, attempts=0))


def frontier_node(channel, client):
    with session_scope() as session:
        node = session.scalar(select(KnowledgeFrontier).where(KnowledgeFrontier.channel_slug == channel["slug"],
            KnowledgeFrontier.is_exhausted.is_(False)).order_by(KnowledgeFrontier.depth_level,
            KnowledgeFrontier.times_used, KnowledgeFrontier.created_at).limit(1))
        if node:
            return node
        roots = session.scalars(select(KnowledgeFrontier.topic_name).where(KnowledgeFrontier.channel_slug == channel["slug"],
            KnowledgeFrontier.parent_id.is_(None))).all()
    proposals = generate_model(Topics, f"Propose five real broad academic topics for {channel['niche']}. Never repeat ANY historical root: {roots}")
    _insert_topics(channel["slug"], proposals.topics, client=client)
    with session_scope() as session:
        node = session.scalar(select(KnowledgeFrontier).where(KnowledgeFrontier.channel_slug == channel["slug"],
            KnowledgeFrontier.is_exhausted.is_(False)).order_by(KnowledgeFrontier.depth_level, KnowledgeFrontier.times_used).limit(1))
    if not node:
        raise RuntimeError("No grounded frontier roots available; inspect verification backlog / search credentials")
    return node


def _consume_node(node, client):
    with session_scope() as session:
        row = session.get(KnowledgeFrontier, node.id, with_for_update=True)
        row.times_used += 1
        exhausted = row.times_used >= 3
        row.is_exhausted = exhausted
    if exhausted and node.depth_level < 3:
        proposals = generate_model(Topics, f"Propose five real academic subtopics of {node.academic_term or node.topic_name}. Supply exact researcher terminology. Depth {node.depth_level + 1}, maximum 3.")
        _insert_topics(node.channel_slug, proposals.topics, parent=node, client=client)


def select_unique_concept(channel, video_id=None, search_client=None, topic=None, options=None):
    from .content_ledger import _try_reserve, embed_text
    from .agents.roles import channel_instructions
    # Owner preference: direct model ideation, without external source verification.
    # Keep the same five-candidate selection and permanent semantic uniqueness checks.
    seed = channel.get('strategy_json', {}).get('topic_seeds') or [channel['niche']]
    collisions = []
    for attempt in range(2):
        candidates = generate_model(UniqueConceptSet,
            f"Channel: {channel['name']}. Niche: {channel['niche']}. Creative direction: {channel.get('strategy_json', {})}. Inspiration: {seed}\n"
            f"Owner instructions: {channel_instructions(channel)}\nRequested topic: {topic or 'Choose from inspiration'}. Run direction: { {k:(options or {}).get(k,'') for k in ('tone','style_note','must_include','must_avoid')} }\n"
            "Respect the requested topic; vary the angle for uniqueness instead of changing to an unrelated topic. Owner instructions override conflicting style defaults.\n"
            "Create exactly five distinct engaging two-sentence concepts directly. No external fact-checking is performed. "
            "Follow the channel format: original fiction or hypothetical conversation where appropriate, established general knowledge for explainers. "
            "Never claim research or verification occurred. Do not invent citations, statistics, real-person quotations or current news. "
            "Frame speculation as speculation, not established fact. "
            "Use canonical entity names. " + (f"Pivot materially away from these collisions: {collisions}" if attempt else ""))
        for candidate in candidates.candidates:
            result = _try_reserve(video_id, candidate, embed_text(candidate.core_concept), None)
            if not result.get("collision"):
                return {"id": result["id"], "core_entity": result["core_entity"], "content_angle": result["content_angle"],
                        "core_concept": result["core_concept"], "academic_term": None}
            collisions.append(candidate.core_concept)
    raise RuntimeError("No unique concept after five candidates and one pivot; manual review required")


def retry_backlog():
    """Retain old records, but do not perform external checks or spend model quota."""
    return {'status':'disabled','reason':'External verification disabled by owner'}
