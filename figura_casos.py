"""
figura_casos.py
==============
Genera dos figuras para el análisis detallado de la memoria:
  · figuras/comp_enfermeria_i11.png : componentes de enfermería (continuidad,
    cualificación, mezcla de edades) en i11, propuesta vs mejor resultado.
  · figuras/comp_ingresos_i08.png   : ingresos por día en i08, propuesta vs
    mejor resultado (distribución temporal de las admisiones).
"""
import json, sys
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

BASE = Path(__file__).parent.resolve()
FIG = BASE / "figuras"; FIG.mkdir(exist_ok=True)
for _s in (sys.stdout,):
    try: _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass

def L(p): return json.load(open(BASE / p, encoding="utf-8"))

COL_N, COL_O = "#2E579C", "#2E9C57"

# ── i11: componentes de enfermería ───────────────────────────────────
dn = L("resultados_rapido_benchmark/detalle_i11.json")
do = L("resultados_oficiales_benchmark/detalle_i11.json")
comps = [("ContinuityOfCare", "Continuidad"), ("RoomSkillLevel", "Cualificación"),
         ("RoomAgeMix", "Mezcla de edades")]
labels = [c[1] for c in comps]
vn = [dn.get(f"cost_{c}", 0) or 0 for c, _ in comps]
vo = [do.get(f"cost_{c}", 0) or 0 for c, _ in comps]

x = range(len(labels)); w = 0.38
plt.figure(figsize=(6, 3.6))
plt.bar([i - w/2 for i in x], vn, w, label="Propuesta", color=COL_N)
plt.bar([i + w/2 for i in x], vo, w, label="Mejor competición", color=COL_O)
for i in x:
    plt.text(i - w/2, vn[i], str(vn[i]), ha="center", va="bottom", fontsize=8)
    plt.text(i + w/2, vo[i], str(vo[i]), ha="center", va="bottom", fontsize=8)
plt.xticks(list(x), labels)
plt.ylabel("coste")
plt.title("i11 — costes de enfermería y edad: propuesta vs mejor", fontsize=10)
plt.legend(fontsize=8)
plt.tight_layout()
plt.savefig(FIG / "comp_enfermeria_i11.png", dpi=160); plt.close()

# ── i08: distribución de ingresos por día ────────────────────────────
ij = L("instancias/i08.json"); D = ij["days"]
def adm(pats):
    a = [0] * D
    for p in pats:
        v = p.get("admission_day")
        if v is None or str(v).lower() == "none":
            continue
        a[int(v)] += 1
    return a
an = adm(L("resultados_rapido/i08/fase3.json")["patients"])
ao = adm(L("soluciones_oficiales/sol_8.json")["patients"])

dias = list(range(D)); w = 0.42
plt.figure(figsize=(10, 3.6))
plt.bar([d - w/2 for d in dias], an, w, label="Propuesta", color=COL_N)
plt.bar([d + w/2 for d in dias], ao, w, label="Mejor competición", color=COL_O)
plt.axvline(D/2 - 0.5, color="#888", ls=":", lw=1)
plt.annotate("21 ingresos\nel último día", xy=(27, max(an)), xytext=(21, max(an)-2),
             fontsize=8, color=COL_N, arrowprops=dict(arrowstyle="->", color=COL_N))
plt.xlabel("día del horizonte"); plt.ylabel("ingresos")
plt.title("i08 — distribución de ingresos por día: propuesta vs mejor", fontsize=10)
plt.xticks(dias, fontsize=7); plt.legend(fontsize=8)
plt.tight_layout()
plt.savefig(FIG / "comp_ingresos_i08.png", dpi=160); plt.close()

print("i11 enfermería:", dict(zip(labels, zip(vn, vo))))
print("i08 ingresos 1a/2a mitad propuesta:", sum(an[:D//2]), sum(an[D//2:]),
      "| mejor:", sum(ao[:D//2]), sum(ao[D//2:]))
print("Figuras: comp_enfermeria_i11.png, comp_ingresos_i08.png")
