"""
ejecutar_rapido.py
==================
Versión reducida de ejecutar_maximo.py orientada a ~7 horas para las 30
instancias (~14 min/instancia). Reutiliza TODA la lógica del pipeline de
calidad máxima (feedback, poda, reloj global, CSV reanudable) cambiando
únicamente los presupuestos de tiempo.

Sigue usando:
  · Todos los hilos de CPU para Gurobi
  · MIPGap = 0.01 (1 %, ligeramente más holgado para ir más rápido)

Reparto por instancia (~840 s):
  · Fase 1  : 4 min   (admisión MIP)
  · Fase 2  : 2,5 min hab + 1,5 min quirófanos
  · Fase 3  : 5 min SA + 1 min búsqueda local final

Salidas en carpetas propias:
  resultados_rapido/
  resultados_rapido_benchmark/

Uso (idéntico a ejecutar_maximo.py):
  python ejecutar_rapido.py
  python ejecutar_rapido.py --desde i08
  python ejecutar_rapido.py --instancias i17,i22
  python ejecutar_rapido.py --forzar
"""

from pathlib import Path
import ejecutar_maximo as em

# ─────────────────────────────────────────────
# SOBREESCRITURA DE PRESUPUESTOS (~14 min/instancia → ~7 h total)
# ─────────────────────────────────────────────

em.MIP_GAP   = 0.01      # 1 % de gap (más rápido que 0,5 %)

em.SA_T0     = 100.0     # algo menos de exploración (presupuesto corto)
em.SA_ALPHA  = 0.9995    # enfriamiento adaptado a ~5 min de SA
em.SA_TMIN   = 1e-4

em.T_F1      = 240       # 4 min   – admisión
em.T_F2_HAB  = 150       # 2,5 min – habitaciones
em.T_F2_OT   = 90        # 1,5 min – quirófanos
em.T_SA      = 300       # 5 min   – simulated annealing
em.T_LS      = 60        # 1 min   – búsqueda local final
em.T_MAX_TOTAL = em.T_F1 + em.T_F2_HAB + em.T_F2_OT + em.T_SA + em.T_LS  # ~840 s

# Feedback reducido: en instancias difíciles el bucle de feedback rara vez
# converge y consume el tiempo que necesita la poda. Con 2 intentos basta para
# comprobar si el feedback ayuda; si no, se pasa antes a la poda (más fiable).
em.MAX_ITER_FEEDBACK = 2
em.MAX_ITER_PODA     = 8

# ─────────────────────────────────────────────
# CARPETAS PROPIAS (no pisar las de calidad máxima)
# ─────────────────────────────────────────────

em.RES_DIR = em.BASE_DIR / "resultados_rapido"
em.OUT_DIR = em.BASE_DIR / "resultados_rapido_benchmark"


if __name__ == "__main__":
    em.main()
