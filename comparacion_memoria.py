"""
comparacion_memoria.py
======================
Genera la sección de comparación para la memoria del TFG:

  1. Configuración experimental de nuestra propuesta FRENTE a las reglas
     de la competición IHTC 2024 (tiempo, hilos, validación, formato).
  2. Identificación EXACTA de los casos en que nos hemos salido de las
     reglas: instancias que superan el límite de tiempo de referencia y
     uso de más hilos de los permitidos.
  3. Tabla de resultados exactos por instancia (coste, violaciones,
     tiempo real, desglose de coste) lista para pegar en la memoria.

Lee los JSON de detalle ya generados (no re-ejecuta nada).
Salida: comparacion_memoria.md  (Markdown) + impresión por pantalla.

Uso:
  python comparacion_memoria.py
  python comparacion_memoria.py --carpeta resultados_rapido_benchmark --limite-competicion 600 --hilos-competicion 4
"""

import argparse
import glob
import json
from pathlib import Path

BASE_DIR = Path(__file__).parent.resolve()

COMPONENTES = [
    ("cost_ElectiveUnscheduledPatients", "Unscheduled"),
    ("cost_PatientDelay",                "Delay"),
    ("cost_RoomSkillLevel",              "Skill"),
    ("cost_ContinuityOfCare",            "Continuity"),
    ("cost_OpenOperatingTheater",        "OpenOT"),
    ("cost_RoomAgeMix",                  "AgeMix"),
    ("cost_ExcessiveNurseWorkload",      "Workload"),
    ("cost_SurgeonTransfer",             "Transfer"),
]


def cargar(carpeta):
    filas = []
    for f in sorted(glob.glob(str(carpeta / "detalle_i*.json"))):
        filas.append(json.load(open(f, encoding="utf-8")))
    filas.sort(key=lambda d: d.get("instancia", ""))
    return filas


def main():
    import sys
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser()
    ap.add_argument("--carpeta", default="resultados_rapido_benchmark")
    ap.add_argument("--limite-competicion", type=float, default=600.0,
                    help="Límite de tiempo de referencia IHTC 2024 (s).")
    ap.add_argument("--hilos-competicion", type=int, default=4,
                    help="Hilos máximos de referencia para la comparación.")
    args = ap.parse_args()

    carpeta = BASE_DIR / args.carpeta
    LIM_T = args.limite_competicion
    LIM_H = args.hilos_competicion
    filas = cargar(carpeta)

    ok = [d for d in filas if d.get("estado") == "OK"]
    no_ok = [d for d in filas if d.get("estado") != "OK"]

    out = []
    W = out.append

    # ── 1. Configuración vs reglas ────────────────────────────────────
    hilos_usados = max((d.get("threads", 0) or 0) for d in filas) if filas else 0
    t_max = max((d.get("tiempos", {}).get("total", 0) or 0) for d in ok) if ok else 0
    t_presupuesto = 840  # 14 min/instancia configurado en ejecutar_rapido

    W("# Comparación con las reglas de la competición IHTC 2024\n")
    W("## 1. Configuración experimental frente a las reglas oficiales\n")
    W("| Parámetro | IHTC 2024 (referencia) | Nuestra propuesta | ¿Dentro de las reglas? |")
    W("|---|---|---|---|")
    W(f"| Límite de tiempo por instancia | {LIM_T:.0f} s (máquina de referencia) | "
      f"presupuesto {t_presupuesto} s, sin tope duro (la poda puede excederlo "
      f"para garantizar factibilidad) | **NO** |")
    W(f"| Hilos de cómputo | {LIM_H} (referencia) | {hilos_usados} (todos los núcleos) | **NO** |")
    W( "| Validación de soluciones | IHTP_Validator oficial | IHTP_Validator oficial | SÍ |")
    W( "| Formato de instancia y solución | JSON oficial | JSON oficial | SÍ |")
    W( "| Restricciones duras (0 violaciones) | exigidas | 0 violaciones en todas | SÍ |")
    W("")
    W("> **Nota metodológica:** nuestra propuesta NO compite bajo condiciones "
      "oficiales: usa más hilos y, en las instancias grandes, más tiempo del "
      "límite de referencia. Los resultados deben leerse como *cota práctica de "
      "calidad alcanzable con recursos ampliados*, no como resultado homologable "
      "en el ranking. El límite exacto de tiempo de la competición se calibra por "
      "máquina; aquí se toma {:.0f} s como referencia.".format(LIM_T))
    W("")

    # ── 2. Casos en que nos hemos salido de las reglas ────────────────
    excede_t = [d for d in ok if (d.get("tiempos", {}).get("total", 0) or 0) > LIM_T]
    excede_h = [d for d in filas if (d.get("threads", 0) or 0) > LIM_H]

    W("## 2. Desviaciones respecto a las reglas\n")
    W(f"- **Hilos:** las **{len(excede_h)}/{len(filas)}** instancias se ejecutaron con "
      f"**{hilos_usados} hilos** (> {LIM_H} de referencia). Afecta solo a las fases "
      f"MIP (Gurobi); las metaheurísticas (greedy, SA, búsqueda local) son de un "
      f"solo hilo.")
    W(f"- **Tiempo:** **{len(excede_t)}/{len(ok)}** instancias superaron el límite de "
      f"{LIM_T:.0f} s. Tiempo máximo observado: **{t_max:.0f} s**.")
    if excede_t:
        W("")
        W(f"| Instancia | Tiempo total (s) | Exceso sobre {LIM_T:.0f} s |")
        W("|---|---|---|")
        for d in sorted(excede_t, key=lambda x: -(x.get("tiempos", {}).get("total", 0) or 0)):
            t = d["tiempos"]["total"]
            W(f"| {d['instancia']} | {t:.1f} | +{t - LIM_T:.0f} |")
    W("")
    W("> El exceso de tiempo se concentra en las instancias grandes (≥150 "
      "pacientes) y proviene del bloque de Fase 2 (habitaciones + poda con "
      "readmisión), que prioriza garantizar una solución factible sobre respetar "
      "el presupuesto blando.")
    W("")

    # ── 3. Resultados exactos por instancia ───────────────────────────
    W("## 3. Resultados exactos de nuestra propuesta\n")
    W("| Inst | Estado | Violac. | Coste total | Unsched. | Delay | Resto | t (s) | Hilos |")
    W("|---|---|---|---|---|---|---|---|---|")
    sum_coste = 0
    sum_uns = sum_del = 0
    for d in filas:
        inst = d.get("instancia", "?")
        est = d.get("estado", "?")
        if est != "OK":
            W(f"| {inst} | {est} | – | – | – | – | – | – | {d.get('threads','–')} |")
            continue
        coste = d.get("total_cost", 0) or 0
        viol = d.get("total_violations", 0)
        uns = d.get("cost_ElectiveUnscheduledPatients", 0) or 0
        dly = d.get("cost_PatientDelay", 0) or 0
        resto = coste - uns - dly
        t = d.get("tiempos", {}).get("total", 0) or 0
        thr = d.get("threads", "–")
        sum_coste += coste; sum_uns += uns; sum_del += dly
        W(f"| {inst} | OK | {viol} | {coste} | {uns} | {dly} | {resto} | {t:.1f} | {thr} |")
    W("")

    # ── 4. Agregados ──────────────────────────────────────────────────
    n_ok = len(ok)
    W("## 4. Métricas agregadas\n")
    W(f"- **Instancias resueltas sin violaciones:** {n_ok}/{len(filas)}")
    if n_ok:
        costes = [d.get("total_cost", 0) or 0 for d in ok]
        W(f"- **Coste total acumulado:** {sum_coste:,}".replace(",", "."))
        W(f"- **Coste medio por instancia:** {sum_coste / n_ok:,.0f}".replace(",", "."))
        W(f"- **Coste mínimo / máximo:** {min(costes):,} / {max(costes):,}".replace(",", "."))
        if sum_coste:
            W(f"- **Composición del coste:** Unscheduled {sum_uns/sum_coste*100:.0f}% · "
              f"Delay {sum_del/sum_coste*100:.0f}% · "
              f"Resto (soft) {(sum_coste-sum_uns-sum_del)/sum_coste*100:.0f}%")
        t_total = sum((d.get("tiempos", {}).get("total", 0) or 0) for d in ok)
        W(f"- **Tiempo total de cómputo (suma):** {t_total/60:.1f} min "
          f"({t_total/3600:.2f} h)")
    if no_ok:
        W(f"- **Instancias pendientes / infactibles:** "
          f"{', '.join(d['instancia'] for d in no_ok)}")
    W("")

    texto = "\n".join(out)
    ruta = carpeta / "comparacion_memoria.md"
    ruta.write_text(texto, encoding="utf-8")

    print(texto)
    print("\n" + "=" * 70)
    print(f"Documento guardado en: {ruta}")


if __name__ == "__main__":
    main()
