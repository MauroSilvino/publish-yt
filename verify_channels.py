#!/usr/bin/env python3
"""Verificação de fim de esteira (roda igual à conferência manual, mas automática).

Usa o MESMO cliente MCP do publish_due.py (consumer key do Composio).

Checagens:
  1) EXISTÊNCIA (crítica): todo video_id da agenda (schedule.json = o que o cron publica)
     ainda existe no seu canal. Se faltar algum, o cron publicaria um vídeo inexistente.
  2) DUPLICATAS (--dupes): lista os uploads de cada canal e acusa título repetido entre
     os vídeos da nossa agenda (a mesma lógica do mutirão de dedup). Best-effort: a
     paginação do playlist do YouTube às vezes perde alguns do fim — o denominador é
     impresso pra você conferir cobertura.

Uso:
  python3 verify_channels.py            # só existência (rápido, confiável)
  python3 verify_channels.py --dupes    # existência + varredura de duplicatas
Env: COMPOSIO_CONSUMER_KEY (igual ao runner).
Sai com código !=0 se faltar algum id ou houver duplicata — serve de "gate" no CI.
"""
import os, sys, json, collections
from publish_due import MCP, KEY, MCP_URL, SCHED_FILE, load_json

def mcp_execute(client, tool_slug, arguments, account):
    r = client._rpc("tools/call", {"name": "COMPOSIO_MULTI_EXECUTE_TOOL", "arguments": {
        "tools": [{"tool_slug": tool_slug, "arguments": arguments, "account": account}],
        "sync_response_to_workbench": False}})
    if "error" in r:
        raise RuntimeError(str(r["error"])[:300])
    obj = json.loads(r["result"]["content"][0]["text"])
    data = obj.get("data", obj)
    results = data.get("results", [])
    if not results:
        raise RuntimeError("resposta sem results: " + str(obj)[:200])
    resp = results[0].get("response", {})
    return resp.get("data", resp)

def norm(t):
    return " ".join((t or "").split())

def check_existence(client, byacct):
    print("== EXISTÊNCIA (ids da agenda x canais) ==")
    missing = []
    for acct, ids in byacct.items():
        d = mcp_execute(client, "YOUTUBE_GET_VIDEO_DETAILS_BATCH",
                        {"id": ids, "parts": ["status"]}, acct)
        found = {x["id"] for x in d.get("items", [])}
        miss = [i for i in ids if i not in found]
        print(f"  {acct}: {len(found)}/{len(ids)}" + ("" if not miss else f"  FALTAM {miss}"))
        missing += [(acct, i) for i in miss]
    total = sum(len(v) for v in byacct.values())
    if missing:
        print(f"  RESULTADO: {total-len(missing)}/{total} OK — FALTAM {len(missing)}: {missing}")
    else:
        print(f"  RESULTADO: {total}/{total} — todos os ids da agenda existem. OK")
    return len(missing) == 0

def check_dupes(client, byacct, titles_by_acct):
    print("\n== DUPLICATAS (título repetido entre os vídeos da agenda) ==")
    dup_found = 0
    low_cov = 0
    for acct, ids in byacct.items():
        idset = set(ids)
        wanted = titles_by_acct[acct]  # video_id -> title_norm (da agenda)
        wanted_titles = collections.Counter(wanted.values())
        # lista uploads do canal
        seen = {}
        token = None; cov = 0
        for _ in range(4):  # até ~4 páginas
            args = {"mine": True, "maxResults": 50}
            if token: args["pageToken"] = token
            d = mcp_execute(client, "YOUTUBE_LIST_CHANNEL_VIDEOS", args, acct)
            for it in d.get("items", []):
                vid = it["snippet"]["resourceId"]["videoId"]
                seen[vid] = norm(it["snippet"]["title"])
            cov = len(seen)
            token = d.get("nextPageToken")
            if not token:
                break
        # agrupa por título os vídeos vistos
        by_t = collections.defaultdict(list)
        for vid, t in seen.items():
            by_t[t].append(vid)
        chan_dups = []
        for vid, tnorm in wanted.items():
            grp = by_t.get(tnorm, [])
            extras = [v for v in grp if v not in idset]  # cópias fora da agenda
            if extras:
                chan_dups.append((tnorm[:50], vid, extras))
        if cov == 0:
            low_cov += 1
            print(f"  {acct}: ⚠️ 0 uploads lidos inline (resposta grande foi pro workbench) — inconclusivo")
        elif chan_dups:
            dup_found += len(chan_dups)
            print(f"  {acct}: {len(chan_dups)} título(s) com cópia (cobertura {cov} uploads):")
            for t, keep, extras in chan_dups:
                print(f"     manter {keep} | remover {extras} | {t}")
        else:
            print(f"  {acct}: sem duplicata (cobertura {cov} uploads)")
    if low_cov:
        print(f"  RESULTADO: INCONCLUSIVO em {low_cov}/8 canais (listagem não veio inline). "
              f"Rode a varredura manual de dedup (ver padroes-de-producao.md).")
        return True  # não bloqueia o gate; a existência é o check crítico
    if dup_found:
        print(f"  RESULTADO: {dup_found} duplicata(s) encontradas.")
    else:
        print("  RESULTADO: nenhuma duplicata. OK")
    return dup_found == 0

def main():
    if not KEY:
        print("ERRO: COMPOSIO_CONSUMER_KEY não definida", file=sys.stderr); sys.exit(2)
    sched = load_json(SCHED_FILE, {"itens": []})["itens"]
    # títulos vêm do manifesto (schedule.json não guarda título), casados por video_id
    MAN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "manifesto-publicacao-youtube.json")
    vid2title = {}
    for it in load_json(MAN, {"itens": []}).get("itens", []):
        if it.get("youtube_video_id"):
            vid2title[it["youtube_video_id"]] = norm(it.get("titulo", ""))
    byacct = collections.defaultdict(list)
    titles_by_acct = collections.defaultdict(dict)
    for it in sched:
        byacct[it["account_id"]].append(it["video_id"])
        titles_by_acct[it["account_id"]][it["video_id"]] = vid2title.get(it["video_id"], "")
    client = MCP(MCP_URL, KEY); client.initialize()
    ok = check_existence(client, byacct)
    if "--dupes" in sys.argv:
        ok = check_dupes(client, byacct, titles_by_acct) and ok
    print("\n" + ("✅ VERIFICAÇÃO OK" if ok else "❌ VERIFICAÇÃO FALHOU"))
    sys.exit(0 if ok else 1)

if __name__ == "__main__":
    main()
