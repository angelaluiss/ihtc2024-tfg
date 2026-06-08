"""
experimentos_semillas_fase3.py

Ejecuta automáticamente varias semillas de la fase 3
(Greedy + Simulated Annealing + búsqueda local final), valida cada solución
con el validador oficial y selecciona la mejor solución factible.

Uso básico desde PowerShell:

    cd C:\\Users\\angel\\OneDrive\\Escritorio\\tfg
    py experimentos_semillas_fase3.py

Por defecto usa:
    - test01.json
    - solucion_fase2.json
    - modelo_enfermeras_sa.py
    - IHTP_Validator.exe
    - semillas 1,2,3,4,5

Salida:
    resultados_fase3_semillas/
        solucion_fase3_sa_seed1.json
        solucion_fase3_sa_seed2.json
        ...
        resumen_semillas.csv
        resumen_semillas.json
        mejor_solucion.json

También copia la mejor solución a:
    solucion_final_test01.json
"""

from pathlib import Path
import argparse
import csv
import json
import re
import shutil
import subprocess
import sys
import time


# ============================================================
# RUTAS POR DEFECTO
# ============================================================

BASE_DIR = Path(r"C:\Users\angel\OneDrive\Escritorio\tfg")

DEFAULT_INSTANCE = BASE_DIR / "test01.json"
DEFAULT_INPUT_PHASE2 = BASE_DIR / "solucion_fase2.json"
DEFAULT_SA_SCRIPT = BASE_DIR / "modelo_enfermeras_sa.py"
DEFAULT_VALIDATOR = BASE_DIR / "IHTP_Validator.exe"

DEFAULT_OUT_DIR = BASE_DIR / "resultados_fase3_semillas"
DEFAULT_BEST_OUTPUT = BASE_DIR / "solucion_final_test01.json"


# ============================================================
# UTILIDADES
# ============================================================

def parse_seeds(text):
    """
    Convierte '1,2,3' en [1,2,3].
    También acepta '1-5', que se interpreta como [1,2,3,4,5].
    """
    text = str(text).strip()

    if "-" in text and "," not in text:
        a, b = text.split("-", 1)
        return list(range(int(a), int(b) + 1))

    return [int(x.strip()) for x in text.split(",") if x.strip()]


def save_json(data, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


def write_csv(rows, path, columns):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({c: row.get(c) for c in columns})


def parse_validator_output(text):
    """
    Extrae métricas del validador oficial.
    """
    violations = {}
    costs = {}
    total_violations = None
    total_cost = None
    section = None

    for raw in text.splitlines():
        line = raw.strip()

        if not line:
            continue

        if line.startswith("VIOLATIONS"):
            section = "violations"
            continue

        if line.startswith("COSTS"):
            section = "costs"
            continue

        if line.startswith("Total violations"):
            m = re.search(r"Total violations\s*=\s*([0-9]+)", line)
            if m:
                total_violations = int(m.group(1))
            continue

        if line.startswith("Total cost"):
            m = re.search(r"Total cost\s*=\s*([0-9]+)", line)
            if m:
                total_cost = int(m.group(1))
            continue

        if section == "violations":
            m = re.match(r"([A-Za-z]+)\.*\s*([0-9]+)", line)
            if m:
                violations[m.group(1)] = int(m.group(2))
            continue

        if section == "costs":
            m = re.match(
                r"([A-Za-z]+)\.*\s*([0-9]+)\s*\(\s*([0-9]+)\s*X\s*([0-9]+)\s*\)",
                line
            )
            if m:
                costs[m.group(1)] = {
                    "weighted_cost": int(m.group(2)),
                    "weight": int(m.group(3)),
                    "raw_cost": int(m.group(4)),
                }

    return {
        "total_violations": total_violations,
        "total_cost": total_cost,
        "violations": violations,
        "costs": costs,
    }


def run_validator(validator, instance, solution):
    """
    Ejecuta el validador oficial sobre una solución.
    """
    validator = Path(validator)
    instance = Path(instance)
    solution = Path(solution)

    if not validator.exists():
        return {
            "ok": False,
            "returncode": None,
            "stdout": "",
            "stderr": f"No existe el validador: {validator}",
            "parsed": {
                "total_violations": None,
                "total_cost": None,
                "violations": {},
                "costs": {},
            },
        }

    if not solution.exists():
        return {
            "ok": False,
            "returncode": None,
            "stdout": "",
            "stderr": f"No existe la solución: {solution}",
            "parsed": {
                "total_violations": None,
                "total_cost": None,
                "violations": {},
                "costs": {},
            },
        }

    cmd = [str(validator), str(instance), str(solution)]

    res = subprocess.run(
        cmd,
        cwd=str(instance.parent),
        capture_output=True,
        text=True,
        timeout=180,
        shell=False,
    )

    text = (res.stdout or "") + "\n" + (res.stderr or "")

    return {
        "ok": res.returncode == 0,
        "returncode": res.returncode,
        "stdout": res.stdout,
        "stderr": res.stderr,
        "parsed": parse_validator_output(text),
    }


def weighted_cost(parsed, name):
    return parsed.get("costs", {}).get(name, {}).get("weighted_cost")


def raw_cost(parsed, name):
    return parsed.get("costs", {}).get(name, {}).get("raw_cost")


def violation(parsed, name):
    return parsed.get("violations", {}).get(name)


# ============================================================
# EJECUCIÓN DE UNA SEMILLA
# ============================================================

def ejecutar_semilla(
    seed,
    instance,
    input_phase2,
    sa_script,
    validator,
    out_dir,
    sa_time_limit,
    sa_max_iter,
    sa_T0,
    sa_alpha,
    sa_Tmin,
    sa_candidates_per_iter,
    ls_final_time,
    ls_final_max_iter,
    ls_final_max_no_improve,
    ls_final_candidates_per_iter,
):
    """
    Ejecuta modelo_enfermeras_sa.py con una semilla concreta y valida.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    solution_path = out_dir / f"solucion_fase3_sa_seed{seed}.json"
    stats_path = out_dir / f"estadisticas_fase3_sa_seed{seed}.json"
    log_path = out_dir / f"log_seed{seed}.txt"
    validator_path = out_dir / f"validator_seed{seed}.json"

    cmd = [
        sys.executable,
        str(sa_script),
        "--instancia", str(instance),
        "--entrada", str(input_phase2),
        "--salida", str(solution_path),
        "--estadisticas", str(stats_path),
        "--validador", str(validator),
        "--seed", str(seed),
        "--sa-time-limit", str(sa_time_limit),
        "--sa-max-iter", str(sa_max_iter),
        "--sa-T0", str(sa_T0),
        "--sa-alpha", str(sa_alpha),
        "--sa-Tmin", str(sa_Tmin),
        "--sa-candidates-per-iter", str(sa_candidates_per_iter),
        "--ls-final-time", str(ls_final_time),
        "--ls-final-max-iter", str(ls_final_max_iter),
        "--ls-final-max-no-improve", str(ls_final_max_no_improve),
        "--ls-final-candidates-per-iter", str(ls_final_candidates_per_iter),
    ]

    print("\n" + "=" * 80)
    print(f"[RUN] Semilla {seed}")
    print("=" * 80)
    print("[CMD]", " ".join(cmd))

    start = time.time()

    res = subprocess.run(
        cmd,
        cwd=str(Path(instance).parent),
        capture_output=True,
        text=True,
        shell=False,
    )

    elapsed = time.time() - start

    log_text = (
        "CMD:\n" + " ".join(cmd) + "\n\n"
        + "RETURN CODE:\n" + str(res.returncode) + "\n\n"
        + "STDOUT:\n" + (res.stdout or "") + "\n\n"
        + "STDERR:\n" + (res.stderr or "") + "\n"
    )

    log_path.write_text(log_text, encoding="utf-8")

    # Aunque el script SA ya valida, volvemos a validar aquí para tener
    # un resumen homogéneo.
    val = run_validator(validator, instance, solution_path)
    save_json(val, validator_path)

    parsed = val["parsed"]

    row = {
        "seed": seed,
        "script_returncode": res.returncode,
        "elapsed_seconds": round(elapsed, 3),
        "solution_path": str(solution_path),
        "stats_path": str(stats_path),
        "log_path": str(log_path),
        "validator_path": str(validator_path),
        "validator_ok": val["ok"],
        "total_violations": parsed.get("total_violations"),
        "total_cost": parsed.get("total_cost"),
        "RoomGenderMix_viol": violation(parsed, "RoomGenderMix"),
        "PatientRoomCompatibility_viol": violation(parsed, "PatientRoomCompatibility"),
        "SurgeonOvertime_viol": violation(parsed, "SurgeonOvertime"),
        "OperatingTheaterOvertime_viol": violation(parsed, "OperatingTheaterOvertime"),
        "MandatoryUnscheduledPatients_viol": violation(parsed, "MandatoryUnscheduledPatients"),
        "AdmissionDay_viol": violation(parsed, "AdmissionDay"),
        "RoomCapacity_viol": violation(parsed, "RoomCapacity"),
        "NursePresence_viol": violation(parsed, "NursePresence"),
        "UncoveredRoom_viol": violation(parsed, "UncoveredRoom"),
        "RoomAgeMix": weighted_cost(parsed, "RoomAgeMix"),
        "RoomSkillLevel": weighted_cost(parsed, "RoomSkillLevel"),
        "ContinuityOfCare": weighted_cost(parsed, "ContinuityOfCare"),
        "ExcessiveNurseWorkload": weighted_cost(parsed, "ExcessiveNurseWorkload"),
        "OpenOperatingTheater": weighted_cost(parsed, "OpenOperatingTheater"),
        "SurgeonTransfer": weighted_cost(parsed, "SurgeonTransfer"),
        "PatientDelay": weighted_cost(parsed, "PatientDelay"),
        "ElectiveUnscheduledPatients": weighted_cost(parsed, "ElectiveUnscheduledPatients"),
        "RoomAgeMix_raw": raw_cost(parsed, "RoomAgeMix"),
        "RoomSkillLevel_raw": raw_cost(parsed, "RoomSkillLevel"),
        "ContinuityOfCare_raw": raw_cost(parsed, "ContinuityOfCare"),
        "ExcessiveNurseWorkload_raw": raw_cost(parsed, "ExcessiveNurseWorkload"),
        "OpenOperatingTheater_raw": raw_cost(parsed, "OpenOperatingTheater"),
        "SurgeonTransfer_raw": raw_cost(parsed, "SurgeonTransfer"),
        "PatientDelay_raw": raw_cost(parsed, "PatientDelay"),
        "ElectiveUnscheduledPatients_raw": raw_cost(parsed, "ElectiveUnscheduledPatients"),
    }

    print("[RESULT] seed:", seed)
    print("         total_violations:", row["total_violations"])
    print("         total_cost:", row["total_cost"])
    print("         RoomSkillLevel:", row["RoomSkillLevel"])
    print("         ContinuityOfCare:", row["ContinuityOfCare"])
    print("         ExcessiveNurseWorkload:", row["ExcessiveNurseWorkload"])

    return row


# ============================================================
# SELECCIÓN DE MEJOR SOLUCIÓN
# ============================================================

def seleccionar_mejor(rows):
    """
    Selecciona la mejor solución factible:
        primero total_violations = 0,
        luego menor total_cost.
    """
    factibles = [
        r for r in rows
        if r.get("total_violations") == 0 and r.get("total_cost") is not None
    ]

    if factibles:
        return min(factibles, key=lambda r: r["total_cost"])

    # Si ninguna es factible, se escoge menor número de violaciones y coste.
    candidatas = [
        r for r in rows
        if r.get("total_violations") is not None and r.get("total_cost") is not None
    ]

    if candidatas:
        return min(candidatas, key=lambda r: (r["total_violations"], r["total_cost"]))

    return None


# ============================================================
# MAIN
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Ejecuta varias semillas de la fase 3 SA y selecciona la mejor solución."
    )

    parser.add_argument("--instancia", default=str(DEFAULT_INSTANCE), help="Ruta de la instancia JSON.")
    parser.add_argument("--entrada", default=str(DEFAULT_INPUT_PHASE2), help="Ruta de solucion_fase2.json.")
    parser.add_argument("--sa-script", default=str(DEFAULT_SA_SCRIPT), help="Ruta de modelo_enfermeras_sa.py.")
    parser.add_argument("--validador", default=str(DEFAULT_VALIDATOR), help="Ruta de IHTP_Validator.exe.")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR), help="Carpeta de salida.")
    parser.add_argument("--best-output", default=str(DEFAULT_BEST_OUTPUT), help="Ruta donde copiar la mejor solución.")

    parser.add_argument("--seeds", default="1,2,3,4,5", help="Semillas, por ejemplo '1,2,3,4,5' o '1-10'.")

    # Parámetros SA.
    parser.add_argument("--sa-time-limit", type=float, default=180, help="Tiempo máximo de SA por semilla.")
    parser.add_argument("--sa-max-iter", type=int, default=60000, help="Iteraciones máximas de SA.")
    parser.add_argument("--sa-T0", type=float, default=50.0, help="Temperatura inicial.")
    parser.add_argument("--sa-alpha", type=float, default=0.9995, help="Factor de enfriamiento.")
    parser.add_argument("--sa-Tmin", type=float, default=1e-4, help="Temperatura mínima.")
    parser.add_argument("--sa-candidates-per-iter", type=int, default=1, help="Vecinos por iteración de SA.")

    # Parámetros LS final.
    parser.add_argument("--ls-final-time", type=float, default=60, help="Tiempo máximo de búsqueda local final.")
    parser.add_argument("--ls-final-max-iter", type=int, default=15000, help="Iteraciones máximas de búsqueda local final.")
    parser.add_argument("--ls-final-max-no-improve", type=int, default=3000, help="Iteraciones sin mejora de búsqueda local final.")
    parser.add_argument("--ls-final-candidates-per-iter", type=int, default=50, help="Vecinos por iteración de búsqueda local final.")

    args = parser.parse_args()

    instance = Path(args.instancia)
    input_phase2 = Path(args.entrada)
    sa_script = Path(args.sa_script)
    validator = Path(args.validador)
    out_dir = Path(args.out_dir)
    best_output = Path(args.best_output)
    seeds = parse_seeds(args.seeds)

    print("=" * 80)
    print("EXPERIMENTOS FASE 3 CON VARIAS SEMILLAS")
    print("=" * 80)
    print("[INFO] Instancia:", instance)
    print("[INFO] Entrada fase 2:", input_phase2)
    print("[INFO] Script SA:", sa_script)
    print("[INFO] Validador:", validator)
    print("[INFO] Carpeta salida:", out_dir)
    print("[INFO] Semillas:", seeds)

    if not instance.exists():
        raise FileNotFoundError(f"No existe la instancia: {instance}")

    if not input_phase2.exists():
        raise FileNotFoundError(f"No existe la solución fase 2: {input_phase2}")

    if not sa_script.exists():
        raise FileNotFoundError(f"No existe el script SA: {sa_script}")

    out_dir.mkdir(parents=True, exist_ok=True)

    rows = []

    for seed in seeds:
        row = ejecutar_semilla(
            seed=seed,
            instance=instance,
            input_phase2=input_phase2,
            sa_script=sa_script,
            validator=validator,
            out_dir=out_dir,
            sa_time_limit=args.sa_time_limit,
            sa_max_iter=args.sa_max_iter,
            sa_T0=args.sa_T0,
            sa_alpha=args.sa_alpha,
            sa_Tmin=args.sa_Tmin,
            sa_candidates_per_iter=args.sa_candidates_per_iter,
            ls_final_time=args.ls_final_time,
            ls_final_max_iter=args.ls_final_max_iter,
            ls_final_max_no_improve=args.ls_final_max_no_improve,
            ls_final_candidates_per_iter=args.ls_final_candidates_per_iter,
        )
        rows.append(row)

    columns = [
        "seed",
        "script_returncode",
        "elapsed_seconds",
        "validator_ok",
        "total_violations",
        "total_cost",
        "RoomSkillLevel",
        "ContinuityOfCare",
        "ExcessiveNurseWorkload",
        "RoomAgeMix",
        "OpenOperatingTheater",
        "SurgeonTransfer",
        "PatientDelay",
        "ElectiveUnscheduledPatients",
        "RoomSkillLevel_raw",
        "ContinuityOfCare_raw",
        "ExcessiveNurseWorkload_raw",
        "RoomAgeMix_raw",
        "OpenOperatingTheater_raw",
        "SurgeonTransfer_raw",
        "PatientDelay_raw",
        "ElectiveUnscheduledPatients_raw",
        "UncoveredRoom_viol",
        "NursePresence_viol",
        "RoomCapacity_viol",
        "AdmissionDay_viol",
        "MandatoryUnscheduledPatients_viol",
        "OperatingTheaterOvertime_viol",
        "SurgeonOvertime_viol",
        "PatientRoomCompatibility_viol",
        "RoomGenderMix_viol",
        "solution_path",
        "stats_path",
        "log_path",
        "validator_path",
    ]

    csv_path = out_dir / "resumen_semillas.csv"
    json_path = out_dir / "resumen_semillas.json"

    write_csv(rows, csv_path, columns)
    save_json(rows, json_path)

    best = seleccionar_mejor(rows)

    if best is None:
        print("[WARNING] No se pudo seleccionar una mejor solución.")
    else:
        best_solution = Path(best["solution_path"])

        if best_solution.exists():
            shutil.copy(best_solution, best_output)
            shutil.copy(best_solution, out_dir / "mejor_solucion.json")

        best_summary = {
            "best_seed": best["seed"],
            "best_total_violations": best["total_violations"],
            "best_total_cost": best["total_cost"],
            "best_solution_path": str(best_solution),
            "copied_to": str(best_output),
            "row": best,
        }

        save_json(best_summary, out_dir / "mejor_solucion_resumen.json")

        print("\n" + "=" * 80)
        print("MEJOR SOLUCIÓN")
        print("=" * 80)
        print("[BEST] seed:", best["seed"])
        print("[BEST] total_violations:", best["total_violations"])
        print("[BEST] total_cost:", best["total_cost"])
        print("[BEST] solution:", best_solution)
        print("[BEST] copied to:", best_output)

    print("\n" + "=" * 80)
    print("EXPERIMENTOS TERMINADOS")
    print("=" * 80)
    print("[OUT] CSV:", csv_path)
    print("[OUT] JSON:", json_path)


if __name__ == "__main__":
    main()
