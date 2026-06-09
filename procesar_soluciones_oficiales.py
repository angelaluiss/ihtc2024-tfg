"""
procesar_soluciones_oficiales.py
================================
Convierte las soluciones OFICIALES de la competición (JSON, mismo formato que
las nuestras) al mismo esquema de detalle/CSV que usamos para nuestros
resultados, ejecutando el validador oficial para obtener el desglose de coste.

Para cada instancia:
  1. Localiza el fichero de solución oficial en la carpeta indicada
     (admite nombres sol_iXX.json, iXX.json, sol_iXX_*.json, etc.).
  2. Ejecuta IHTP_Validator.exe instancia.json solucion_oficial.json
     y extrae los 8 componentes de coste y las violaciones.
  3. Calcula la estructura de la solución (pacientes programados, ocupación de
     camas, uso de quirófanos, mapa habitación×día) reutilizando
     generar_dashboard.datos_solucion.
  4. Escribe resultados_oficiales_benchmark/detalle_iXX.json (mismo esquema que
     nuestros detalle) e informe.csv.

Además compara el coste del validador con el best-known publicado en
resultados_oficiales.json (control de consistencia).

Uso:
  python procesar_soluciones_oficiales.py
  python procesar_soluciones_oficiales.py --soluciones soluciones_oficiales
"""

import argparse
import csv
import glob
import json
import re
import subprocess
import sys
from pathlib import Path

import generar_dashboard as gd   # reutilizamos datos_solucion()

BASE = Path(__file__).parent.resolve()

# Nombres EXACTOS de las categorías del IHTP_Validator
CATS = ["RoomAgeMix", "RoomSkillLevel", "ContinuityOfCare", "ExcessiveNurseWorkload",
        "OpenOperatingTheater", "SurgeonTransfer", "PatientDelay", "ElectiveUnscheduledPatients"]


def parse_validator(text):
    costs, viol, total_cost, total_viol = {}, {}, None, None
    section = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("VIOLATIONS"):
            section = "v"; continue
        if line.startswith("COSTS"):
            section = "c"; continue
        if line.startswith("Total violations"):
            m = re.search(r"=\s*([0-9]+)", line); total_viol = int(m.group(1)) if m else None; continue
        if line.startswith("Total cost"):
            m = re.search(r"=\s*([0-9]+)", line); total_cost = int(m.group(1)) if m else None; continue
        if section == "v":
            m = re.match(r"([A-Za-z]+)\.*\s*([0-9]+)", line)
            if m:
                viol[m.group(1)] = int(m.group(2))
        if section == "c":
            m = re.match(r"([A-Za-z]+)\.*\s*([0-9]+)\s*\(\s*([0-9]+)\s*X\s*([0-9]+)\s*\)", line)
            if m:
                costs[m.group(1)] = int(m.group(2))
    return total_cost, total_viol, costs, viol


def localizar_solucion(carpeta, inst, n):
    """Busca el fichero de solución oficial para la instancia (varios nombres).

    Admite tanto la forma con etiqueta de instancia (sol_i01.json) como la
    numérica sin cero a la izquierda (sol_1.json), que es la que usa la web
    oficial de la competición.
    """
    patrones = [
        f"sol_{inst}.json", f"{inst}.json",          # sol_i01.json / i01.json
        f"sol_{n}.json", f"{n}.json",                # sol_1.json / 1.json
        f"sol_{n:02d}.json", f"{n:02d}.json",        # sol_01.json / 01.json
        f"sol_{inst}_*.json", f"sol_{n}_*.json", f"*{inst}*.json",
    ]
    for pat in patrones:
        hits = sorted(glob.glob(str(carpeta / pat)))
        if hits:
            return Path(hits[0])
    return None


def main():
    for _s in (sys.stdout,):
        try: _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception: pass

    ap = argparse.ArgumentParser()
    ap.add_argument("--soluciones", default="soluciones_oficiales",
                    help="Carpeta con las soluciones oficiales (sol_iXX.json).")
    ap.add_argument("--instancias", default="instancias")
    ap.add_argument("--validador", default="IHTP_Validator.exe")
    ap.add_argument("--salida", default="resultados_oficiales_benchmark")
    ap.add_argument("--bks", default="resultados_oficiales.json")
    args = ap.parse_args()

    carpeta_sol = BASE / args.soluciones
    carpeta_inst = BASE / args.instancias
    validador = BASE / args.validador
    salida = BASE / args.salida
    salida.mkdir(parents=True, exist_ok=True)

    if not carpeta_sol.exists():
        print(f"[ERROR] No existe la carpeta de soluciones: {carpeta_sol}")
        print("Crea la carpeta y coloca dentro las soluciones oficiales (sol_iXX.json).")
        return
    if not validador.exists():
        print(f"[ERROR] No se encuentra el validador: {validador}")
        return

    bks = {}
    if (BASE / args.bks).exists():
        bks = json.load(open(BASE / args.bks, encoding="utf-8")).get("instancias", {})

    filas = []
    print(f"{'inst':>5} {'estado':>10} {'coste':>8} {'BKS':>8} {'viol':>5}")
    for n in range(1, 31):
        inst = f"i{n:02d}"
        ruta_inst = carpeta_inst / f"{inst}.json"
        ruta_sol = localizar_solucion(carpeta_sol, inst, n)
        det = {"instancia": inst, "estado": "SIN_SOLUCION", "fuente": "IHTC2024 oficial"}

        if ruta_sol is None or not ruta_inst.exists():
            print(f"{inst:>5} {'sin sol':>10}")
            filas.append(det); continue

        try:
            res = subprocess.run([str(validador), str(ruta_inst), str(ruta_sol)],
                                 capture_output=True, text=True, timeout=120)
            txt = (res.stdout or "") + "\n" + (res.stderr or "")
            tc, tv, costs, viol = parse_validator(txt)

            det["estado"] = "OK" if tv == 0 else "VIOLACIONES"
            det["total_cost"] = tc
            det["total_violations"] = tv
            for c in CATS:
                det[f"cost_{c}"] = costs.get(c, 0)
            for c, v in viol.items():
                det[f"viol_{c}"] = v

            # Estructura de la solución (reutilizamos datos_solucion del dashboard)
            ij = json.load(open(ruta_inst, encoding="utf-8"))
            sj = json.load(open(ruta_sol, encoding="utf-8"))
            det["solucion"] = gd.datos_solucion(ij, sj)

            b = bks.get(inst, {}).get("best_known")
            aviso = ""
            if b is not None and tc is not None and tc != b:
                aviso = f"  (!) BKS publicado={b}, validador={tc}"
            print(f"{inst:>5} {det['estado']:>10} {str(tc):>8} {str(b):>8} {str(tv):>5}{aviso}")
        except Exception as e:
            det["estado"] = "ERROR"
            det["error"] = str(e)
            print(f"{inst:>5} {'ERROR':>10}  {e}")

        filas.append(det)
        with open(salida / f"detalle_{inst}.json", "w", encoding="utf-8") as f:
            json.dump(det, f, indent=4, ensure_ascii=False)

    # CSV resumen
    cols = ["instancia", "estado", "total_violations", "total_cost"] + [f"cost_{c}" for c in CATS]
    with open(salida / "informe.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for d in filas:
            w.writerow(d)

    ok = [d for d in filas if d.get("estado") == "OK"]
    print("-" * 50)
    print(f"Procesadas: {len(ok)}/30 con solución oficial válida")
    print(f"Detalles en: {salida}")
    print(f"CSV: {salida / 'informe.csv'}")


if __name__ == "__main__":
    main()
