#!/usr/bin/env python3
"""Verifica a saude do publish.yml (o cron que troca private->public). So stdlib.
Recebe o estado do workflow e do ultimo run (o workflow.yml que chama isto ja tem o `gh`
disponivel pra buscar isso) e cruza com schedule.json/state.json pra ver se o que deveria
ter sido publicado ha pouco realmente foi.

Uso: vigia_publicacao.py --workflow-state <state> --last-run-conclusion <concl>
                          --last-run-minutes-ago <n> [--min-date AAAA-MM-DD]
Saida: relatorio em texto (stdout). Exit 0 = saudavel, 1 = problema.
"""
import argparse, json, os, sys
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
LAST_RUN_MAX_AGE_MIN = 40   # cron e' a cada 10min; 40min de folga pra atraso do GitHub
ATRASADOS_LIMIT = 3         # quantos itens vencidos ha >30min sem publicar disparam alerta
GRACE_MIN = 30              # tolerancia antes de considerar um item "atrasado"

p = argparse.ArgumentParser()
p.add_argument("--workflow-state", required=True)
p.add_argument("--last-run-conclusion", default="")
p.add_argument("--last-run-minutes-ago", type=float, default=99999)
p.add_argument("--min-date", default="")
args = p.parse_args()

problemas = []

if args.workflow_state != "active":
    problemas.append(f"workflow publish.yml NAO esta ativo (state={args.workflow_state}) "
                      f"-- alguem desligou e ninguem religou.")

if args.last_run_minutes_ago > LAST_RUN_MAX_AGE_MIN:
    problemas.append(f"ultimo run foi ha {args.last_run_minutes_ago:.0f} min (limite {LAST_RUN_MAX_AGE_MIN}) "
                      f"-- o cron parou de disparar.")

sched = json.load(open(os.path.join(HERE, "schedule.json"), encoding="utf-8"))["itens"]
state = json.load(open(os.path.join(HERE, "state.json"), encoding="utf-8"))["publicados"]
agora = datetime.now(timezone.utc)
corte = agora - timedelta(minutes=GRACE_MIN)

atrasados = [
    s for s in sched
    if s["video_id"] not in state
    and (not args.min_date or s["data_alvo"] >= args.min_date)
    and datetime.strptime(s["publish_at_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) <= corte
]

if len(atrasados) >= ATRASADOS_LIMIT:
    exemplos = ", ".join(f'{s["canal"]}/{s["data_alvo"]}/{s["peca"]}' for s in atrasados[:5])
    problemas.append(f"{len(atrasados)} itens vencidos ha mais de {GRACE_MIN}min e ainda privados "
                      f"(ex.: {exemplos}) -- publicacao nao esta acompanhando a agenda.")

if problemas:
    print(f"[{agora.isoformat()}] PROBLEMA detectado:")
    for pr in problemas:
        print(f"  - {pr}")
    print(f"\nContexto: workflow_state={args.workflow_state} "
          f"ultimo_run_conclusion={args.last_run_conclusion or 'desconhecido'} "
          f"ultimo_run_ha={args.last_run_minutes_ago:.0f}min atrasados={len(atrasados)}")
    sys.exit(1)

print(f"[{agora.isoformat()}] OK -- workflow ativo, ultimo run ha {args.last_run_minutes_ago:.0f}min "
      f"({args.last_run_conclusion or '?'}), {len(atrasados)} atrasados (limite {ATRASADOS_LIMIT}).")
sys.exit(0)
