#!/usr/bin/env python3
"""Runner de publicação na nuvem (independente do PC).

Lê schedule.json (gerado por gen_schedule.py a partir do manifesto), acha os vídeos
cujo horário-alvo (publish_at_utc) já venceu e ainda não foram publicados, e troca
private -> public no YouTube via o **MCP hospedado do Composio** (autenticado pela
consumer key no header x-consumer-api-key). Guarda o que já publicou em state.json
(idempotente: nunca publica duas vezes, mesmo rodando de novo).

Projetado pra rodar num cron de nuvem (GitHub Actions) a cada ~10 min. Só usa a
stdlib do Python (sem pip install).

Uso:
  python3 publish_due.py                 # publica os vencidos de verdade
  python3 publish_due.py --dry-run       # mostra o que FARIA, sem chamar o MCP
  python3 publish_due.py --check-auth    # testa só a credencial (initialize+tools/list)
  python3 publish_due.py --test-execute <VIDEO_ID>   # no-op: prova o caminho sem publicar

Env obrigatória:
  COMPOSIO_CONSUMER_KEY   consumer key do Composio (header x-consumer-api-key, começa com ck_)
Env opcional:
  COMPOSIO_MCP_URL      default https://connect.composio.dev/mcp
  SCHEDULE_FILE         default ./schedule.json
  STATE_FILE            default ./state.json
  SCHEDULE_MIN_DATE     AAAA-MM-DD; se definida, ignora itens vencidos com data_alvo anterior a
                        essa data (não publica nem marca como feito -- fica pra sempre de fora).
                        Usado pra retomar depois de uma pausa longa sem tentar publicar de uma vez
                        o backlog acumulado.
"""
import os, sys, json, time
from datetime import datetime, timezone
import urllib.request, urllib.error

MCP_URL   = os.environ.get("COMPOSIO_MCP_URL", "https://connect.composio.dev/mcp")

def _resolve_key():
    k = os.environ.get("COMPOSIO_CONSUMER_KEY", "") or os.environ.get("COMPOSIO_API_KEY", "")
    if k:
        return k
    # fallback local (máquina do usuário): chaves-api.json (fora do git). No GitHub Actions
    # esse arquivo não existe, então continua valendo o secret via env acima.
    try:
        p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "chaves-api.json")
        return json.load(open(p, encoding="utf-8")).get("composio_consumer_key", "")
    except Exception:
        return ""

KEY = _resolve_key()
SCHED_FILE = os.environ.get("SCHEDULE_FILE", os.path.join(os.path.dirname(__file__), "schedule.json"))
STATE_FILE = os.environ.get("STATE_FILE", os.path.join(os.path.dirname(__file__), "state.json"))
MIN_DATE = os.environ.get("SCHEDULE_MIN_DATE", "")

def now_utc():
    return datetime.now(timezone.utc)

def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default

def save_state(state):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_FILE)


class MCP:
    """Cliente MCP mínimo (Streamable HTTP + JSON-RPC) pro Composio hospedado."""
    def __init__(self, url, key):
        self.url = url
        self.key = key
        self.session = None
        self._id = 0

    def _rpc(self, method, params=None, notify=False):
        self._id += 1
        payload = {"jsonrpc": "2.0", "method": method}
        if not notify:
            payload["id"] = self._id
        if params is not None:
            payload["params"] = params
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "x-consumer-api-key": self.key,
        }
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        req = urllib.request.Request(self.url, data=json.dumps(payload).encode(),
                                     method="POST", headers=headers)
        with urllib.request.urlopen(req, timeout=90) as r:
            sid = r.headers.get("Mcp-Session-Id") or r.headers.get("mcp-session-id")
            if sid:
                self.session = sid
            raw = r.read().decode(errors="replace")
            if notify:
                return None
            if "text/event-stream" in r.headers.get("Content-Type", ""):
                for line in raw.splitlines():
                    if line.startswith("data:"):
                        try:
                            obj = json.loads(line[5:].strip())
                            if isinstance(obj, dict) and ("result" in obj or "error" in obj):
                                return obj
                        except Exception:
                            pass
                raise RuntimeError(f"resposta SSE sem JSON-RPC: {raw[:200]}")
            return json.loads(raw) if raw.strip() else {}

    def initialize(self):
        r = self._rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                     "clientInfo": {"name": "cloud-publisher", "version": "1.0"}})
        if "error" in r:
            raise RuntimeError(f"initialize falhou: {r['error']}")
        self._rpc("notifications/initialized", notify=True)
        return r

    def list_tools(self):
        r = self._rpc("tools/list")
        return [t["name"] for t in r.get("result", {}).get("tools", [])]

    def execute(self, tool_slug, arguments, account):
        """Executa um tool do Composio via COMPOSIO_MULTI_EXECUTE_TOOL.
        Retorna (ok: bool, detalhe: str)."""
        r = self._rpc("tools/call", {"name": "COMPOSIO_MULTI_EXECUTE_TOOL", "arguments": {
            "tools": [{"tool_slug": tool_slug, "arguments": arguments, "account": account}],
            "sync_response_to_workbench": False,
            "thought": "cloud-publisher flip", "current_step": "PUBLISHING"}})
        if "error" in r:
            return False, f"jsonrpc error: {r['error']}"
        # o payload real vem como texto JSON dentro de result.content[0].text
        try:
            text = r["result"]["content"][0]["text"]
            obj = json.loads(text)
        except Exception as e:
            return False, f"resposta inesperada: {str(r)[:200]} ({e})"
        data = obj.get("data", obj)
        results = data.get("results", [])
        if results:
            resp = results[0].get("response", {})
            if resp.get("successful") is True or data.get("success_count", 0) >= 1:
                return True, "ok"
            return False, results[0].get("error") or resp.get("error") or str(results[0])[:200]
        if data.get("success_count", 0) >= 1:
            return True, "ok"
        return False, str(obj)[:200]


def get_client():
    if not KEY:
        print("ERRO: COMPOSIO_CONSUMER_KEY não definida", file=sys.stderr); sys.exit(2)
    c = MCP(MCP_URL, KEY)
    c.initialize()
    return c

def cmd_check_auth():
    try:
        c = get_client()
        tools = c.list_tools()
        print(f"AUTH OK — MCP respondeu, {len(tools)} tools disponíveis: {', '.join(tools[:8])}")
        return 0
    except urllib.error.HTTPError as e:
        print(f"AUTH FALHOU: HTTP {e.code} — {e.read().decode()[:300]}", file=sys.stderr); return 1
    except Exception as e:
        print(f"AUTH FALHOU: {e}", file=sys.stderr); return 1

def cmd_test_execute(video_id):
    """No-op: chama YOUTUBE_UPDATE_VIDEO só com video_id (nada muda). Se a resposta
    reclamar 'No update fields', o caminho está 100% ok — o flip real é idêntico."""
    sched = load_json(SCHED_FILE, {"itens": []})["itens"]
    acct = next((s["account_id"] for s in sched if s["video_id"] == video_id), None)
    if not acct:
        print(f"video_id {video_id} não está no schedule.json", file=sys.stderr); return 2
    c = get_client()
    ok, det = c.execute("YOUTUBE_UPDATE_VIDEO", {"video_id": video_id}, acct)
    if (not ok) and "No update fields" in det:
        print("EXECUTE OK (no-op) — caminho validado, nada publicado. O flip real usará privacy_status=public.")
        return 0
    if ok:
        print("EXECUTE retornou sucesso — inesperado num no-op, mas o caminho funciona.")
        return 0
    print(f"EXECUTE FALHOU: {det}", file=sys.stderr); return 1

def main():
    dry = "--dry-run" in sys.argv
    if "--check-auth" in sys.argv:
        sys.exit(cmd_check_auth())
    if "--test-execute" in sys.argv:
        sys.exit(cmd_test_execute(sys.argv[sys.argv.index("--test-execute") + 1]))

    sched = load_json(SCHED_FILE, {"itens": []})["itens"]
    state = load_json(STATE_FILE, {"publicados": {}})
    done = state.setdefault("publicados", {})
    agora = now_utc()

    due = [s for s in sched
           if s["video_id"] not in done
           and datetime.strptime(s["publish_at_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) <= agora]
    due.sort(key=lambda s: s["publish_at_utc"])

    pulados_backlog = 0
    if MIN_DATE:
        antes = len(due)
        due = [s for s in due if s["data_alvo"] >= MIN_DATE]
        pulados_backlog = antes - len(due)

    print(f"[{agora.isoformat()}] agenda={len(sched)} já_publicados={len(done)} vencidos_agora={len(due)}"
          + (f" ignorados_backlog(<{MIN_DATE})={pulados_backlog}" if MIN_DATE else ""))
    if not due:
        print("nada a publicar."); return

    if dry:
        for s in due:
            print(f"  [DRY] publicaria {s['canal']}/{s['data_alvo']}/{s['peca']} ({s['video_id']}) alvo={s['publish_at_utc']}")
        return

    c = get_client()
    ok = err = 0
    for s in due:
        tag = f'{s["canal"]}/{s["data_alvo"]}/{s["peca"]} ({s["video_id"]})'
        try:
            good, det = c.execute("YOUTUBE_UPDATE_VIDEO",
                                  {"video_id": s["video_id"], "privacy_status": "public"},
                                  s["account_id"])
            if not good:
                raise RuntimeError(det)
            done[s["video_id"]] = {"quando": now_utc().strftime("%Y-%m-%dT%H:%M:%SZ"),
                                   "canal": s["canal"], "peca": s["peca"], "data_alvo": s["data_alvo"]}
            save_state(state)  # salva a cada sucesso -> nunca republica
            print(f"  OK  {tag}"); ok += 1
            time.sleep(1)
        except Exception as e:
            print(f"  ERRO {tag}: {e}", file=sys.stderr); err += 1
    print(f"resumo: {ok} publicados, {err} erros.")
    if err:
        sys.exit(1)

if __name__ == "__main__":
    main()
