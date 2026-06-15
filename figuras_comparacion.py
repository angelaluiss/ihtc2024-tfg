"""
figuras_comparacion.py
=====================
Genera la figura de comparación por componentes de coste entre nuestra
propuesta y las soluciones oficiales, a partir de los detalles ya procesados.

Salida: figuras/comparacion_componentes.png
"""
import glob, json, sys
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

BASE = Path(__file__).parent.resolve()
FIG = BASE / "figuras"; FIG.mkdir(exist_ok=True)
for _s in (sys.stdout,):
    try: _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass

COMP = [("ElectiveUnscheduledPatients", "Unscheduled"), ("PatientDelay", "Delay"),
        ("ContinuityOfCare", "Continuity"), ("RoomSkillLevel", "Skill"),
        ("OpenOperatingTheater", "OpenOT"), ("ExcessiveNurseWorkload", "Workload"),
        ("RoomAgeMix", "AgeMix"), ("SurgeonTransfer", "Transfer")]

def cargar(carpeta):
    r = {}
    for f in glob.glob(str(BASE / carpeta / "detalle_i*.json")):
        d = json.load(open(f, encoding="utf-8")); r[d["instancia"]] = d
    return r

nu = cargar("resultados_rapido_benchmark")
of = cargar("resultados_oficiales_benchmark")
insts = [i for i in sorted(set(nu) & set(of))
         if of[i].get("estado") == "OK" and nu[i].get("total_cost")]

labels = [t for _, t in COMP]
sn = [sum(nu[i].get(f"cost_{c}", 0) or 0 for i in insts) for c, _ in COMP]
so = [sum(of[i].get(f"cost_{c}", 0) or 0 for i in insts) for c, _ in COMP]

x = range(len(labels)); w = 0.4
plt.figure(figsize=(9, 4))
plt.bar([i - w/2 for i in x], sn, w, label="Propuesta", color="#2E579C")
plt.bar([i + w/2 for i in x], so, w, label="Oficial", color="#2E9C57")
plt.xticks(list(x), labels, rotation=30, ha="right", fontsize=9)
plt.ylabel("Coste agregado")
plt.title(f"Coste por componente: propuesta vs oficial ({len(insts)} instancias)", fontsize=10)
plt.legend()
plt.tight_layout()
plt.savefig(FIG / "comparacion_componentes.png", dpi=160)
plt.close()

print(f"Instancias comparadas: {len(insts)}")
tot_n, tot_o = sum(sn), sum(so)
print(f"Total propuesta {tot_n} | oficial {tot_o} | sobrecoste +{(tot_n-tot_o)/tot_o*100:.0f}%")
print("Figura: figuras/comparacion_componentes.png")
