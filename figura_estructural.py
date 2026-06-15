"""
figura_estructural.py
=====================
Genera una figura que reproduce la vista estructural del cuadro de mandos
(ocupación de camas e ingresos por día, propuesta vs oficial) para una
instancia congestionada. Sirve de ilustración en la memoria.

Salida: figuras/estructural_<inst>.png
"""
import json, sys
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import generar_dashboard as gd

BASE = Path(__file__).parent.resolve()
FIG = BASE / "figuras"; FIG.mkdir(exist_ok=True)
for _s in (sys.stdout,):
    try: _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass

INST = sys.argv[1] if len(sys.argv) > 1 else "i27"

ij = json.load(open(BASE / "instancias" / f"{INST}.json", encoding="utf-8"))
sn = gd.datos_solucion(ij, json.load(open(BASE / "resultados_rapido" / INST / "fase3.json", encoding="utf-8")))
do = json.load(open(BASE / "resultados_oficiales_benchmark" / f"detalle_{INST}.json", encoding="utf-8"))
so = do["solucion"]
dias = list(range(sn["days"]))

fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 3.8))

ax1.plot(dias, sn["bed"], color="#2E579C", label="Propuesta", lw=2)
ax1.plot(dias, so["bed"], color="#2E9C57", label="Oficial", lw=2)
ax1.axhline(sn["total_beds"], color="#C03030", ls="--", lw=1, label="Capacidad")
ax1.set_title(f"Ocupación de camas por día ({INST})", fontsize=10)
ax1.set_xlabel("día"); ax1.set_ylabel("camas ocupadas"); ax1.legend(fontsize=8)

w = 0.4
ax2.bar([d - w/2 for d in dias], sn["adm"], w, color="#2E579C", label="Propuesta")
ax2.bar([d + w/2 for d in dias], so["adm"], w, color="#2E9C57", label="Oficial")
ax2.set_title(f"Ingresos por día ({INST})", fontsize=10)
ax2.set_xlabel("día"); ax2.set_ylabel("ingresos"); ax2.legend(fontsize=8)

plt.tight_layout()
out = FIG / f"estructural_{INST}.png"
plt.savefig(out, dpi=160); plt.close()
print(f"Propuesta: {sn['n_sched']} programados / {sn['n_unsched']} descartados")
print(f"Oficial:   {so['n_sched']} programados / {so['n_unsched']} descartados")
print(f"Figura: {out}")
