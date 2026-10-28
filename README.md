# ELSEWHERE

> *Feel at home, elsewhere.*

Type what home feels like + the city you just landed in. A generic LLM gives you TripAdvisor. **ELSEWHERE** shows you where you'll belong: a map of spots in the new city where people with overlapping taste actually gather — each with the reason attached.

Built for the [Qloo Agentic Hackathon](https://qloo.devpost.com/).

## How it works

One prompt in, two panes out:

- **Tourist mode** — what a generic LLM says with your words. Top-10 lists, stereotypes, no reasons.
- **Transplant mode** — what the Qloo-grounded agent finds with the same words *plus* taste-graph signals: a Leaflet map, 5 transplant cards with affinity badges ("2.4× your graph"), and the taste path that connects *this you* → *this spot*.

The same LLM writes both pane summaries. The difference is the evidence — and the honesty toggle shows it.

## Status

🚧 Under active construction for the Oct 31, 2026 deadline. Architecture: FastAPI (AG-UI 1.0 agent endpoint + Qloo proxy) · React + Vite + beUI · OpenRouter. See [`.agents/PRDs/PRD.md`](.agents/PRDs/PRD.md) for the full product spec.

## Quickstart

```sh
git clone <repo-url> && cd elsewhere
python -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
cp .env.example .env
uvicorn backend.app.main:app --port 8000
curl localhost:8000/api/health
curl localhost:8000/
docker compose up --build
pytest backend/tests -q
```

## License

[MIT](LICENSE)
