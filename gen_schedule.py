#!/usr/bin/env python3
"""Gera cloud-publisher/schedule.json a partir do manifesto de publicação.

Cada item vira {video_id, account_id, canal, peca, data_alvo, publish_at_utc}.
O horário UTC é calculado do horario_local + timezone de cada item, com DST
correto (zoneinfo) — então o horário efetivo pro público fica exato o ano todo,
inclusive nas trocas de horário de verão dos EUA (o cron local antigo não fazia).

Uso:
  python3 gen_schedule.py [data_ini] [data_fim]   # default 2026-08-10 2026-08-22
Só entram itens com youtube_video_id preenchido (já subiram como private).
"""
import json, os, sys
from datetime import datetime
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
RC   = os.path.dirname(HERE)  # Recursos Compartilhados
MAN  = os.path.join(RC, "manifesto-publicacao-youtube.json")
CFG  = os.path.join(RC, "config-publicacao-youtube.json")
OUT  = os.path.join(HERE, "schedule.json")

def main():
    ini = sys.argv[1] if len(sys.argv) > 1 else "2026-08-10"
    fim = sys.argv[2] if len(sys.argv) > 2 else "2026-09-23"  # cobre 10/08..23/09 (só entram itens com video_id)
    itens = json.load(open(MAN, encoding="utf-8"))["itens"]
    canais = json.load(open(CFG, encoding="utf-8"))["canais"]
    acct = {k: v["composio_account_id"] for k, v in canais.items()}

    sched, sem_id = [], 0
    for it in itens:
        if not (ini <= it["data_publicacao_alvo"] <= fim):
            continue
        vid = it.get("youtube_video_id")
        if not vid:
            sem_id += 1; continue
        tz = ZoneInfo(it["timezone"])
        local = datetime.strptime(f'{it["data_publicacao_alvo"]} {it["horario_local"]}',
                                  "%Y-%m-%d %H:%M").replace(tzinfo=tz)
        utc = local.astimezone(ZoneInfo("UTC"))
        sched.append({
            "video_id": vid, "account_id": acct[it["canal"]], "canal": it["canal"],
            "peca": it["peca"], "data_alvo": it["data_publicacao_alvo"],
            "publish_at_utc": utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "publish_at_local": f'{it["data_publicacao_alvo"]} {it["horario_local"]} {it["timezone"]}',
        })
    sched.sort(key=lambda x: x["publish_at_utc"])
    json.dump({"gerado_de": "manifesto-publicacao-youtube.json", "janela": [ini, fim],
               "total": len(sched), "itens": sched},
              open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"schedule.json: {len(sched)} itens ({ini}..{fim}) | sem video_id: {sem_id}")

if __name__ == "__main__":
    main()
