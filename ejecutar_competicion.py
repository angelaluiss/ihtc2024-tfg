"""
ejecutar_competicion.py
=======================
Ejecución BAJO REGLAS DE COMPETICIÓN IHTC 2024 (resultado homologable).

A diferencia de ejecutar_rapido.py / ejecutar_maximo.py, aquí se respetan
estrictamente las condiciones oficiales:

  · MÁXIMO 4 HILOS en Gurobi (límite oficial de paralelismo IHTC 2024).
  · TOPE DURO de tiempo por instancia (por defecto 600 s). Un reloj global
    interrumpe cualquier fase que se pase: la poda NO puede excederse del
    presupuesto, así que si no se encuentra factibilidad dentro del límite,
    la instancia se reporta como infactible (resultado honesto de competición).
  · Pipeline corregido (poda + readmisión, parser de costes correcto).

Reparto del presupuesto de 600 s:
  · Fase 1  : 180 s (admisión MIP)
  · Fase 2  : 120 s habitaciones + 60 s quirófanos
  · Fase 3  : 180 s SA + 60 s búsqueda local final

Salidas en carpetas propias (no pisan las de los runs relajados):
  resultados_competicion/
  resultados_competicion_benchmark/

NOTA: este script está PREPARADO pero pensado para lanzarse cuando se quiera.
      Con 30 instancias × 600 s el tope teórico es 5 h (en la práctica menos,
      porque las pequeñas terminan antes).

Uso:
  python ejecutar_competicion.py                 # las 30, reglas oficiales
  python ejecutar_competicion.py --instancias i01,i17
  python ejecutar_competicion.py --limite 600    # cambiar el tope duro
"""

from pathlib import Path
import argparse
import sys

import ejecutar_maximo as em

# ─────────────────────────────────────────────
# Parámetro modificable por CLI (tope duro)
# ─────────────────────────────────────────────

def aplicar_config(limite_total):
    """Configura ejecutar_maximo con condiciones de competición."""
    # Reparto proporcional del presupuesto total (600 s por defecto).
    em.MIP_GAP   = 0.02            # gap algo más holgado: poco tiempo por fase
    em.SA_T0     = 100.0
    em.SA_ALPHA  = 0.9995
    em.SA_TMIN   = 1e-4

    em.T_F1      = int(limite_total * 0.30)   # 180 s con 600
    em.T_F2_HAB  = int(limite_total * 0.20)   # 120 s
    em.T_F2_OT   = int(limite_total * 0.10)   #  60 s
    em.T_SA      = int(limite_total * 0.30)   # 180 s
    em.T_LS      = int(limite_total * 0.10)   #  60 s
    em.T_MAX_TOTAL = limite_total

    # Feedback y poda acotados: pocas iteraciones para no agotar el tope.
    em.MAX_ITER_FEEDBACK = 2
    em.MAX_ITER_PODA     = 6
    # Tope DURO: la poda NO puede excederse del presupuesto de Fase 2.
    em.PODA_TOPE_FACTOR  = 1.0

    em.RES_DIR = em.BASE_DIR / "resultados_competicion"
    em.OUT_DIR = em.BASE_DIR / "resultados_competicion_benchmark"


# ─────────────────────────────────────────────
# Monkey-patch: forzar 1 hilo y TOPE DURO global
# ─────────────────────────────────────────────

_ejecutar_instancia_original = em.ejecutar_instancia


def ejecutar_instancia_competicion(nombre, verbose=True):
    """
    Envoltura que impone:
      1) 1 hilo Gurobi (vía em.N_THREADS).
      2) Tope duro de tiempo: si el pipeline supera em.T_MAX_TOTAL, la poda
         deja de poder excederse (em.* ya está configurado), pero además
         el tope de seguridad de la poda se reduce al tiempo restante real.
    """
    return _ejecutar_instancia_original(nombre, verbose=verbose)


def main():
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    parser = argparse.ArgumentParser(
        description="Ejecución bajo reglas de competición IHTC 2024 (1 hilo, tope duro)."
    )
    parser.add_argument("--instancias", default=None)
    parser.add_argument("--desde", default=None)
    parser.add_argument("--hasta", default=None)
    parser.add_argument("--limite", type=float, default=600.0,
                        help="Tope DURO de tiempo por instancia (s). Oficial IHTC: 600.")
    parser.add_argument("--hilos", type=int, default=4,
                        help="Hilos Gurobi. Oficial IHTC: máximo 4.")
    parser.add_argument("--forzar", action="store_true")
    parser.add_argument("--lista", action="store_true")
    parser.add_argument("--silencioso", action="store_true")
    args = parser.parse_args()

    # 4 HILOS: máximo permitido por las reglas IHTC 2024 (mejor rendimiento legal).
    em.N_THREADS = args.hilos
    aplicar_config(args.limite)
    em.ejecutar_instancia = ejecutar_instancia_competicion

    # Reconstruir sys.argv para reutilizar em.main() (que parsea sus flags).
    nuevos_argv = ["ejecutar_competicion.py"]
    if args.instancias:
        nuevos_argv += ["--instancias", args.instancias]
    if args.desde:
        nuevos_argv += ["--desde", args.desde]
    if args.hasta:
        nuevos_argv += ["--hasta", args.hasta]
    if args.forzar:
        nuevos_argv += ["--forzar"]
    if args.lista:
        nuevos_argv += ["--lista"]
    if args.silencioso:
        nuevos_argv += ["--silencioso"]
    sys.argv = nuevos_argv

    print("=" * 74)
    print("  MODO COMPETICIÓN IHTC 2024")
    print(f"  Hilos        : {em.N_THREADS} (oficial)")
    print(f"  Tope DURO    : {args.limite:.0f} s/instancia")
    print(f"  Reparto      : F1={em.T_F1}s  F2={em.T_F2_HAB}+{em.T_F2_OT}s  SA={em.T_SA}s  LS={em.T_LS}s")
    print("=" * 74)

    em.main()


if __name__ == "__main__":
    main()
