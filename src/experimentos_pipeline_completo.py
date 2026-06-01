"""
experimentos_pipeline_completo.py

Ejecuta el flujo completo de la metodología:

    Fase 1  : admisiones
    Fase 2A : habitaciones
    Fase 2B : quirófanos
    Fase 3  : greedy + SA + búsqueda local final, con varias semillas

Sirve para:
    - repetir test01 de forma limpia,
    - ejecutar varias instancias i01, i02, ...
    - guardar resultados comparables en CSV/JSON,
    - seleccionar automáticamente la mejor solución final por instancia.

Requisitos en la carpeta del TFG:
    - modelo_scp_gurobi.py
    - modelo_habitaciones_gurobi.py
    - modelo_enfermeras_sa.py
    - experimentos_semillas_fase3.py
    - IHTP_Validator.exe
    - instancias JSON, por ejemplo test01.json, i01.json, ...

Uso básico:

    cd C:\\Users\\angel\\OneDrive\\Escritorio\\tfg
    py experimentos_pipeline_completo.py --instances test01.json

Para varias públicas:

    py experimentos_pipeline_completo.py --instances i01.json,i02.json,i03.json --seeds 1-5

Si las instancias están en una carpeta:

    py experimentos_pipeline_completo.py --instances-dir instances --glob "i0*.json" --seeds 1-3

Salida:
    resultados_pipeline_completo/
        test01/
            iter_00/
            iter_01/
            fase3_semillas/
            solucion_final_test01.json
        resumen_pipeline.csv
        resumen_pipeline.json
"""

from pathlib import Path
import argparse
import csv
import importlib.util
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

DEFAULT_PHASE1_SCRIPT = BASE_DIR / "modelo_scp_gurobi.py"
DEFAULT_PHASE2_SCRIPT = BASE_DIR / "modelo_habitaciones_gurobi.py"
DEFAULT_PHASE3_EXPERIMENTS_SCRIPT = BASE_DIR / "experimentos_semillas_fase3.py"
DEFAULT_VALIDATOR = BASE_DIR / "IHTP_Validator.exe"

DEFAULT_OUT_DIR = BASE_DIR / "resultados_pipeline_completo"


# ============================================================
# UTILIDADES
# ============================================================

def cargar_json(path):
    path = Path(path)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def guardar_json(data, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


def escribir_csv(rows, path, columns):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({c: row.get(c) for c in columns})


def importar_modulo(nombre, ruta):
    ruta = Path(ruta)

    if not ruta.exists():
        raise FileNotFoundError(f"No se encuentra el script: {ruta}")

    spec = importlib.util.spec_from_file_location(nombre, str(ruta))
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def resolver_instancias(args):
    """
    Devuelve una lista de rutas de instancias.
    """
    base_dir = Path(args.base_dir)

    instancias = []

    if args.instances:
        for item in args.instances.split(","):
            item = item.strip()
            if not item:
                continue

            p = Path(item)

            if not p.is_absolute():
                p = base_dir / p

            instancias.append(p)

    if args.instances_dir:
        d = Path(args.instances_dir)
        if not d.is_absolute():
            d = base_dir / d

        glob_pattern = args.glob or "*.json"
        instancias.extend(sorted(d.glob(glob_pattern)))

    # Quitar duplicados preservando orden.
    vistos = set()
    salida = []

    for p in instancias:
        rp = str(p.resolve())
        if rp not in vistos:
            vistos.add(rp)
            salida.append(p)

    if not salida:
        raise ValueError("No se ha indicado ninguna instancia.")

    return salida


# ============================================================
# PARSEO DEL VALIDADOR
# ============================================================

def parse_validator_output(text):
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


def ejecutar_validador(ruta_validador, ruta_instancia, ruta_solucion, timeout=180):
    ruta_validador = Path(ruta_validador)
    ruta_instancia = Path(ruta_instancia)
    ruta_solucion = Path(ruta_solucion)

    if not ruta_validador.exists():
        return {
            "ok": False,
            "returncode": None,
            "stdout": "",
            "stderr": f"No existe el validador: {ruta_validador}",
            "parsed": {"total_violations": None, "total_cost": None, "violations": {}, "costs": {}},
        }

    if not ruta_solucion.exists():
        return {
            "ok": False,
            "returncode": None,
            "stdout": "",
            "stderr": f"No existe la solución: {ruta_solucion}",
            "parsed": {"total_violations": None, "total_cost": None, "violations": {}, "costs": {}},
        }

    cmd = [str(ruta_validador), str(ruta_instancia), str(ruta_solucion)]

    res = subprocess.run(
        cmd,
        cwd=str(ruta_instancia.parent),
        capture_output=True,
        text=True,
        timeout=timeout,
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
# FASES 1 Y 2
# ============================================================

def feedback_tiene_contenido(path):
    """
    Determina si feedback_fase2.json contiene restricciones/penalizaciones.
    """
    path = Path(path)

    if not path.exists():
        return False

    try:
        data = cargar_json(path)
    except Exception:
        return False

    for key in [
        "day_penalties",
        "gender_day_penalties",
        "day_admission_caps",
        "gender_day_admission_caps",
    ]:
        val = data.get(key, {})
        if isinstance(val, dict) and len(val) > 0:
            return True

    return False


def ejecutar_fase1(phase1, instancia_path, salida_path, feedback_path, stats_path, args):
    instancia = phase1.cargar_instancia(str(instancia_path))

    kwargs = {
        "instancia": instancia,
        "ruta_salida": str(salida_path),
        "ruta_feedback": str(feedback_path) if feedback_path is not None else None,
        "time_limit": args.time_fase1,
        "mip_gap": args.mip_gap_fase1,
        "usar_capacidad_genero": not args.sin_capacidad_genero,
        "usar_hall_compatibilidad": not args.sin_hall_habitaciones,
        "usar_hall_quirofanos": not args.sin_hall_quirofanos,
        "feedback_caps_suaves": True,
        "priorizar_opcionales": True,
        "salida_estadisticas": str(stats_path),
    }

    return phase1.resolver_fase1_scp_interactiva(**kwargs)


def ejecutar_fase2(phase2, instancia_path, sol_fase1, sol_fase2, feedback_out, args):
    return phase2.resolver_habitaciones_debug(
        ruta_instancia=str(instancia_path),
        ruta_sol_previa=str(sol_fase1),
        ruta_final=str(sol_fase2),
        ruta_feedback=str(feedback_out),
        time_limit=args.time_fase2,
    )


# ============================================================
# FASE 3 CON SEMILLAS
# ============================================================

def ejecutar_fase3_semillas(instancia_path, sol_fase2, out_dir_instancia, args):
    fase3_dir = out_dir_instancia / "fase3_semillas"
    best_output = out_dir_instancia / f"solucion_final_{instancia_path.stem}.json"

    cmd = [
        sys.executable,
        str(args.phase3_experiments_script),
        "--instancia", str(instancia_path),
        "--entrada", str(sol_fase2),
        "--out-dir", str(fase3_dir),
        "--best-output", str(best_output),
        "--seeds", args.seeds,
        "--sa-time-limit", str(args.sa_time_limit),
        "--sa-max-iter", str(args.sa_max_iter),
        "--sa-T0", str(args.sa_T0),
        "--sa-alpha", str(args.sa_alpha),
        "--sa-Tmin", str(args.sa_Tmin),
        "--sa-candidates-per-iter", str(args.sa_candidates_per_iter),
        "--ls-final-time", str(args.ls_final_time),
        "--ls-final-max-iter", str(args.ls_final_max_iter),
        "--ls-final-max-no-improve", str(args.ls_final_max_no_improve),
        "--ls-final-candidates-per-iter", str(args.ls_final_candidates_per_iter),
    ]

    log_path = out_dir_instancia / "log_fase3_semillas.txt"

    print("[FASE 3]", " ".join(cmd))

    start = time.time()

    res = subprocess.run(
        cmd,
        cwd=str(instancia_path.parent),
        capture_output=True,
        text=True,
        shell=False,
    )

    elapsed = time.time() - start

    log_path.write_text(
        "CMD:\n" + " ".join(cmd) + "\n\n"
        + "RETURN CODE:\n" + str(res.returncode) + "\n\n"
        + "STDOUT:\n" + (res.stdout or "") + "\n\n"
        + "STDERR:\n" + (res.stderr or "") + "\n",
        encoding="utf-8"
    )

    best_summary_path = fase3_dir / "mejor_solucion_resumen.json"

    best_summary = None
    if best_summary_path.exists():
        best_summary = cargar_json(best_summary_path)

    return {
        "returncode": res.returncode,
        "elapsed_seconds": elapsed,
        "fase3_dir": str(fase3_dir),
        "best_output": str(best_output),
        "log_path": str(log_path),
        "best_summary": best_summary,
    }


# ============================================================
# PIPELINE DE UNA INSTANCIA
# ============================================================

def seleccionar_mejor_fase2(rows_iter):
    """
    Selecciona la mejor solución de fase 2 entre iteraciones.

    Como fase 2 todavía no tiene enfermeras, puede tener UncoveredRoom > 0.
    Se elige la de menor coste total si el validador lo devuelve.
    """
    candidatas = [
        r for r in rows_iter
        if r.get("sol_fase2_exists") and r.get("fase2_total_cost") is not None
    ]

    if candidatas:
        return min(candidatas, key=lambda r: r["fase2_total_cost"])

    candidatas = [r for r in rows_iter if r.get("sol_fase2_exists")]

    if candidatas:
        return candidatas[-1]

    return None


def ejecutar_instancia(instancia_path, args, phase1, phase2):
    instancia_path = Path(instancia_path)
    out_dir = Path(args.out_dir)
    inst_dir = out_dir / instancia_path.stem
    inst_dir.mkdir(parents=True, exist_ok=True)

    print("\n" + "#" * 90)
    print(f"# INSTANCIA {instancia_path.name}")
    print("#" * 90)

    rows_iter = []
    feedback_in = None

    for it in range(args.n_iter):
        iter_dir = inst_dir / f"iter_{it:02d}"
        iter_dir.mkdir(parents=True, exist_ok=True)

        sol_fase1 = iter_dir / "solucion_fase1.json"
        sol_fase2 = iter_dir / "solucion_fase2.json"
        feedback_out = iter_dir / "feedback_fase2.json"
        stats_fase1 = iter_dir / "estadisticas_fase1.json"

        print("\n" + "-" * 80)
        print(f"[ITER {it:02d}] Fase 1")
        print("-" * 80)

        t0 = time.time()
        try:
            ejecutar_fase1(
                phase1=phase1,
                instancia_path=instancia_path,
                salida_path=sol_fase1,
                feedback_path=feedback_in,
                stats_path=stats_fase1,
                args=args,
            )
            fase1_error = ""
        except Exception as e:
            fase1_error = repr(e)
            print("[ERROR FASE 1]", fase1_error)

        t_fase1 = time.time() - t0

        print("\n" + "-" * 80)
        print(f"[ITER {it:02d}] Fase 2")
        print("-" * 80)

        t0 = time.time()
        try:
            if sol_fase1.exists():
                ejecutar_fase2(
                    phase2=phase2,
                    instancia_path=instancia_path,
                    sol_fase1=sol_fase1,
                    sol_fase2=sol_fase2,
                    feedback_out=feedback_out,
                    args=args,
                )
                fase2_error = ""
            else:
                fase2_error = "No existe solucion_fase1.json"
        except Exception as e:
            fase2_error = repr(e)
            print("[ERROR FASE 2]", fase2_error)

        t_fase2 = time.time() - t0

        # Validación fase 2.
        val_fase2 = ejecutar_validador(args.validator, instancia_path, sol_fase2)
        guardar_json(val_fase2, iter_dir / "validator_fase2.json")
        parsed2 = val_fase2["parsed"]

        row_it = {
            "iter": it,
            "sol_fase1": str(sol_fase1),
            "sol_fase1_exists": sol_fase1.exists(),
            "sol_fase2": str(sol_fase2),
            "sol_fase2_exists": sol_fase2.exists(),
            "feedback_out": str(feedback_out),
            "feedback_has_content": feedback_tiene_contenido(feedback_out),
            "fase1_error": fase1_error,
            "fase2_error": fase2_error,
            "time_fase1": round(t_fase1, 3),
            "time_fase2": round(t_fase2, 3),
            "fase2_total_violations": parsed2.get("total_violations"),
            "fase2_total_cost": parsed2.get("total_cost"),
            "fase2_UncoveredRoom": violation(parsed2, "UncoveredRoom"),
            "fase2_RoomAgeMix": weighted_cost(parsed2, "RoomAgeMix"),
            "fase2_OpenOperatingTheater": weighted_cost(parsed2, "OpenOperatingTheater"),
            "fase2_SurgeonTransfer": weighted_cost(parsed2, "SurgeonTransfer"),
            "fase2_PatientDelay": weighted_cost(parsed2, "PatientDelay"),
            "fase2_ElectiveUnscheduledPatients": weighted_cost(parsed2, "ElectiveUnscheduledPatients"),
        }

        rows_iter.append(row_it)

        print("[ITER RESULT]")
        print("  fase2_total_violations:", row_it["fase2_total_violations"])
        print("  fase2_total_cost:", row_it["fase2_total_cost"])
        print("  fase2_UncoveredRoom:", row_it["fase2_UncoveredRoom"])
        print("  fase2_RoomAgeMix:", row_it["fase2_RoomAgeMix"])

        # Si hay feedback, se usa en la siguiente iteración.
        # Si no hay feedback, se repite una vez más solo si el usuario lo pidió.
        feedback_in = feedback_out if feedback_tiene_contenido(feedback_out) else None

        if args.parar_si_sin_feedback and feedback_in is None:
            print("[INFO] Feedback vacío. Se detienen iteraciones de fase 1-2.")
            break

    guardar_json(rows_iter, inst_dir / "resumen_iteraciones_fase12.json")

    cols_iter = [
        "iter",
        "sol_fase1_exists",
        "sol_fase2_exists",
        "feedback_has_content",
        "fase2_total_violations",
        "fase2_total_cost",
        "fase2_UncoveredRoom",
        "fase2_RoomAgeMix",
        "fase2_OpenOperatingTheater",
        "fase2_SurgeonTransfer",
        "fase2_PatientDelay",
        "fase2_ElectiveUnscheduledPatients",
        "time_fase1",
        "time_fase2",
        "sol_fase1",
        "sol_fase2",
        "feedback_out",
        "fase1_error",
        "fase2_error",
    ]

    escribir_csv(rows_iter, inst_dir / "resumen_iteraciones_fase12.csv", cols_iter)

    best_fase2 = seleccionar_mejor_fase2(rows_iter)

    if best_fase2 is None:
        print("[WARNING] No se obtuvo ninguna solución de fase 2 para esta instancia.")
        return {
            "instance": instancia_path.name,
            "status": "sin_fase2",
            "error": "No se obtuvo solución de fase 2",
        }

    best_sol_fase2 = Path(best_fase2["sol_fase2"])
    copia_best_fase2 = inst_dir / f"mejor_solucion_fase2_{instancia_path.stem}.json"
    shutil.copy(best_sol_fase2, copia_best_fase2)

    print("\n" + "-" * 80)
    print("[FASE 3] Semillas SA")
    print("-" * 80)

    fase3_info = None
    if not args.skip_fase3:
        fase3_info = ejecutar_fase3_semillas(
            instancia_path=instancia_path,
            sol_fase2=copia_best_fase2,
            out_dir_instancia=inst_dir,
            args=args,
        )

    final_solution = None
    if fase3_info is not None:
        final_solution = Path(fase3_info["best_output"])

    val_final = ejecutar_validador(args.validator, instancia_path, final_solution) if final_solution else None

    if val_final:
        guardar_json(val_final, inst_dir / "validator_final.json")
        parsed_final = val_final["parsed"]
    else:
        parsed_final = {"total_violations": None, "total_cost": None, "violations": {}, "costs": {}}

    best_seed = None
    if fase3_info and fase3_info.get("best_summary"):
        best_seed = fase3_info["best_summary"].get("best_seed")

    row = {
        "instance": instancia_path.name,
        "status": "ok",
        "best_phase2_iter": best_fase2.get("iter"),
        "best_phase2_solution": str(copia_best_fase2),
        "phase2_total_violations": best_fase2.get("fase2_total_violations"),
        "phase2_total_cost": best_fase2.get("fase2_total_cost"),
        "phase2_UncoveredRoom": best_fase2.get("fase2_UncoveredRoom"),
        "phase2_RoomAgeMix": best_fase2.get("fase2_RoomAgeMix"),
        "phase2_OpenOperatingTheater": best_fase2.get("fase2_OpenOperatingTheater"),
        "phase2_SurgeonTransfer": best_fase2.get("fase2_SurgeonTransfer"),
        "phase2_PatientDelay": best_fase2.get("fase2_PatientDelay"),
        "phase2_ElectiveUnscheduledPatients": best_fase2.get("fase2_ElectiveUnscheduledPatients"),
        "best_seed_fase3": best_seed,
        "final_solution": str(final_solution) if final_solution else None,
        "final_total_violations": parsed_final.get("total_violations"),
        "final_total_cost": parsed_final.get("total_cost"),
        "final_RoomAgeMix": weighted_cost(parsed_final, "RoomAgeMix"),
        "final_RoomSkillLevel": weighted_cost(parsed_final, "RoomSkillLevel"),
        "final_ContinuityOfCare": weighted_cost(parsed_final, "ContinuityOfCare"),
        "final_ExcessiveNurseWorkload": weighted_cost(parsed_final, "ExcessiveNurseWorkload"),
        "final_OpenOperatingTheater": weighted_cost(parsed_final, "OpenOperatingTheater"),
        "final_SurgeonTransfer": weighted_cost(parsed_final, "SurgeonTransfer"),
        "final_PatientDelay": weighted_cost(parsed_final, "PatientDelay"),
        "final_ElectiveUnscheduledPatients": weighted_cost(parsed_final, "ElectiveUnscheduledPatients"),
        "final_UncoveredRoom_viol": violation(parsed_final, "UncoveredRoom"),
        "out_dir": str(inst_dir),
    }

    guardar_json(row, inst_dir / "resumen_instancia.json")

    print("\n[RESUMEN INSTANCIA]")
    print("  final_total_violations:", row["final_total_violations"])
    print("  final_total_cost:", row["final_total_cost"])
    print("  best_seed_fase3:", row["best_seed_fase3"])
    print("  final_solution:", row["final_solution"])

    return row


# ============================================================
# MAIN
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="Ejecuta el pipeline completo por fases para una o varias instancias.")

    parser.add_argument("--base-dir", default=str(BASE_DIR), help="Carpeta base del TFG.")
    parser.add_argument("--instances", default="test01.json", help="Lista separada por comas: test01.json,i01.json,...")
    parser.add_argument("--instances-dir", default=None, help="Carpeta con instancias JSON.")
    parser.add_argument("--glob", default="*.json", help="Patrón si se usa --instances-dir, por ejemplo i*.json.")

    parser.add_argument("--phase1-script", default=str(DEFAULT_PHASE1_SCRIPT), help="Ruta de modelo_scp_gurobi.py.")
    parser.add_argument("--phase2-script", default=str(DEFAULT_PHASE2_SCRIPT), help="Ruta de modelo_habitaciones_gurobi.py.")
    parser.add_argument("--phase3-experiments-script", default=str(DEFAULT_PHASE3_EXPERIMENTS_SCRIPT), help="Ruta de experimentos_semillas_fase3.py.")
    parser.add_argument("--validator", default=str(DEFAULT_VALIDATOR), help="Ruta de IHTP_Validator.exe.")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR), help="Carpeta de salida.")

    parser.add_argument("--n-iter", type=int, default=2, help="Iteraciones fase 1-2 con feedback.")
    parser.add_argument("--parar-si-sin-feedback", action="store_true", help="Detener iteraciones si feedback está vacío.")

    parser.add_argument("--time-fase1", type=float, default=120, help="Time limit fase 1.")
    parser.add_argument("--time-fase2", type=float, default=60, help="Time limit fase 2.")
    parser.add_argument("--mip-gap-fase1", type=float, default=0.02, help="MIPGap fase 1.")

    parser.add_argument("--sin-capacidad-genero", action="store_true", help="Desactiva capacidad agregada por género en fase 1.")
    parser.add_argument("--sin-hall-habitaciones", action="store_true", help="Desactiva Hall de habitaciones en fase 1.")
    parser.add_argument("--sin-hall-quirofanos", action="store_true", help="Desactiva Hall de quirófanos en fase 1.")

    parser.add_argument("--skip-fase3", action="store_true", help="No ejecutar fase 3.")
    parser.add_argument("--seeds", default="1-5", help="Semillas de fase 3. Ej: 1-5 o 1,2,3.")
    parser.add_argument("--sa-time-limit", type=float, default=180, help="Tiempo máximo SA por semilla.")
    parser.add_argument("--sa-max-iter", type=int, default=60000, help="Iteraciones máximas SA.")
    parser.add_argument("--sa-T0", type=float, default=50.0, help="Temperatura inicial SA.")
    parser.add_argument("--sa-alpha", type=float, default=0.9995, help="Enfriamiento SA.")
    parser.add_argument("--sa-Tmin", type=float, default=1e-4, help="Temperatura mínima SA.")
    parser.add_argument("--sa-candidates-per-iter", type=int, default=1, help="Vecinos por iteración SA.")

    parser.add_argument("--ls-final-time", type=float, default=60, help="Tiempo búsqueda local final.")
    parser.add_argument("--ls-final-max-iter", type=int, default=15000, help="Iteraciones máximas LS final.")
    parser.add_argument("--ls-final-max-no-improve", type=int, default=3000, help="Sin mejora LS final.")
    parser.add_argument("--ls-final-candidates-per-iter", type=int, default=50, help="Vecinos por iteración LS final.")

    args = parser.parse_args()

    args.base_dir = Path(args.base_dir)
    args.phase1_script = Path(args.phase1_script)
    args.phase2_script = Path(args.phase2_script)
    args.phase3_experiments_script = Path(args.phase3_experiments_script)
    args.validator = Path(args.validator)
    args.out_dir = Path(args.out_dir)

    instancias = resolver_instancias(args)

    print("=" * 90)
    print("PIPELINE COMPLETO IHTC")
    print("=" * 90)
    print("[INFO] Instancias:", [str(p) for p in instancias])
    print("[INFO] Salida:", args.out_dir)

    phase1 = importar_modulo("fase1_admisiones", args.phase1_script)
    phase2 = importar_modulo("fase2_habitaciones_quirofanos", args.phase2_script)

    rows = []

    for instancia_path in instancias:
        row = ejecutar_instancia(instancia_path, args, phase1, phase2)
        rows.append(row)

    columns = [
        "instance",
        "status",
        "best_phase2_iter",
        "phase2_total_violations",
        "phase2_total_cost",
        "phase2_UncoveredRoom",
        "phase2_RoomAgeMix",
        "phase2_OpenOperatingTheater",
        "phase2_SurgeonTransfer",
        "phase2_PatientDelay",
        "phase2_ElectiveUnscheduledPatients",
        "best_seed_fase3",
        "final_total_violations",
        "final_total_cost",
        "final_RoomAgeMix",
        "final_RoomSkillLevel",
        "final_ContinuityOfCare",
        "final_ExcessiveNurseWorkload",
        "final_OpenOperatingTheater",
        "final_SurgeonTransfer",
        "final_PatientDelay",
        "final_ElectiveUnscheduledPatients",
        "final_UncoveredRoom_viol",
        "best_phase2_solution",
        "final_solution",
        "out_dir",
    ]

    args.out_dir.mkdir(parents=True, exist_ok=True)
    escribir_csv(rows, args.out_dir / "resumen_pipeline.csv", columns)
    guardar_json(rows, args.out_dir / "resumen_pipeline.json")

    print("\n" + "=" * 90)
    print("PIPELINE TERMINADO")
    print("=" * 90)
    print("[OUT] CSV:", args.out_dir / "resumen_pipeline.csv")
    print("[OUT] JSON:", args.out_dir / "resumen_pipeline.json")


if __name__ == "__main__":
    main()
