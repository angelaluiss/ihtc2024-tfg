"""
figuras_memoria.py
==================
Genera las figuras y los datos numéricos exactos para el capítulo de
Validación y resultados de la memoria, a partir de los JSON de detalle del
run en condiciones ampliadas (resultados_rapido_benchmark) y los costes
oficiales (resultados_oficiales.json).

Produce:
  figuras/gap_por_instancia.png      → barras de gap % por instancia
  figuras/descomposicion_coste.png   → tarta de composición del coste total
  + impresión de filas LaTeX y agregados exactos.
"""

import glob
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

BASE = Path(__file__).parent.resolve()
FIG = BASE / "figuras"
FIG.mkdir(exist_ok=True)

for _s in (sys.stdout,):
    try: _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass

of = json.load(open(BASE / "resultados_oficiales.json", encoding="utf-8"))["instancias"]
nuestros = {}
for f in sorted(glob.glob(str(BASE / "resultados_rapido_benchmark" / "detalle_i*.json"))):
    d = json.load(open(f, encoding="utf-8"))
    nuestros[d["instancia"]] = d

insts = [f"i{n:02d}" for n in range(1, 31)]
filas = []
sum_uns = sum_del = sum_resto = sum_tot = 0
for inst in insts:
    d = nuestros.get(inst, {})
    bks = of[inst]["best_known"]
    if d.get("estado") == "OK" and d.get("total_cost") is not None:
        c = d["total_cost"]
        gap = (c - bks) / bks * 100
        uns = d.get("cost_ElectiveUnscheduledPatients", 0) or 0
        dly = d.get("cost_PatientDelay", 0) or 0
        resto = c - uns - dly
        sum_uns += uns; sum_del += dly; sum_resto += resto; sum_tot += c
        filas.append((inst, bks, c, gap))
    else:
        filas.append((inst, bks, None, None))

# ── Filas LaTeX ──────────────────────────────────────────────────────
print("=== FILAS LATEX (instancia & oficial & obtenido & gap) ===")
for inst, bks, c, gap in filas:
    if c is None:
        print(f"\\texttt{{{inst}}} & {bks} & --- & --- \\\\")
    else:
        print(f"\\texttt{{{inst}}} & {bks} & {c} & {gap:.2f}\\% \\\\")

# ── Agregados ────────────────────────────────────────────────────────
g = [x[3] for x in filas if x[3] is not None]
print("\n=== AGREGADOS ===")
print(f"Resueltas: {len(g)}/30")
print(f"Gap medio: {sum(g)/len(g):.1f}%  | mediano: {sorted(g)[len(g)//2]:.1f}%")
print(f"Min: {min(g):.1f}% | Max: {max(g):.1f}%")
print(f"<=10%: {sum(1 for x in g if x<=10)} | 10-30%: {sum(1 for x in g if 10<x<=30)} | >30%: {sum(1 for x in g if x>30)}")
print(f"Coste total: {sum_tot}")
print(f"Composicion: Unsched {sum_uns/sum_tot*100:.0f}% | Delay {sum_del/sum_tot*100:.0f}% | Resto {sum_resto/sum_tot*100:.0f}%")

# ── Figura 1: gap por instancia ──────────────────────────────────────
labels = [x[0] for x in filas if x[3] is not None]
vals = [x[3] for x in filas if x[3] is not None]
colores = ["#2E9C57" if v <= 10 else ("#E0A030" if v <= 30 else "#C03030") for v in vals]
plt.figure(figsize=(10, 3.6))
plt.bar(labels, vals, color=colores)
gmed = sum(vals) / len(vals)
plt.axhline(gmed, color="#2E579C", ls="--", lw=1.2, label=f"Gap medio {gmed:.1f}%")
plt.ylabel("Gap relativo (%)")
plt.xticks(rotation=90, fontsize=7)
plt.legend(fontsize=8)
plt.title("Gap relativo frente al mejor resultado oficial (condiciones ampliadas)", fontsize=9)
plt.tight_layout()
plt.savefig(FIG / "gap_por_instancia.png", dpi=160)
plt.close()

# ── Figura 2: descomposición del coste ───────────────────────────────
plt.figure(figsize=(5, 4))
vals2 = [sum_uns, sum_del, sum_resto]
etq = [f"Opcionales\nno programados\n{sum_uns/sum_tot*100:.0f}%",
       f"Retraso\nadmisión\n{sum_del/sum_tot*100:.0f}%",
       f"Costes blandos\nrestantes\n{sum_resto/sum_tot*100:.0f}%"]
plt.pie(vals2, labels=etq, colors=["#C03030", "#E0A030", "#2E9C57"],
        startangle=90, textprops={"fontsize": 8}, wedgeprops={"edgecolor": "white"})
plt.title("Composición del coste total agregado", fontsize=10)
plt.tight_layout()
plt.savefig(FIG / "descomposicion_coste.png", dpi=160)
plt.close()

print("\nFiguras guardadas en:", FIG)
