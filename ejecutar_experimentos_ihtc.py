"""
ejecutar_experimentos_ihtc.py

Orquestador completo para ejecutar la metodología sobre una o varias instancias IHTC:

    Fase 1  : admisión de pacientes
    Fase 2A : asignación de habitaciones
    Fase 2B : asignación de quirófanos
    Fase 3  : asignación de enfermeras con Greedy + Simulated Annealing + búsqueda local final
    Validación oficial
    Comparación con mejores costes publicados de la competición

Este script no sustituye a los modelos. Los importa y los ejecuta:
    - modelo_scp_gurobi.py
    - modelo_habitaciones_gurobi.py
    - modelo_enfermeras_sa.py
    - modelo_enfermeras_greedy_inicio.py
    - modelo_enfermeras_busqueda_local.py

Uso básico desde la carpeta del proyecto:

    py ejecutar_experimentos_ihtc.py --instances instancias/i01.json,instancias/i02.json --seeds 1-3

Uso para todas las instancias públicas en una carpeta:

    py ejecutar_experimentos_ihtc.py --instances-dir instancias --glob "i*.json" --seeds 1-3

Si el validador no está en la carpeta del proyecto:

    py ejecutar_experimentos_ihtc.py --instances-dir instancias --glob "i*.json" --validator C:/ruta/IHTP_Validator.exe

Salidas:
    resultados_experimentos_ihtc/
        resumen_pipeline.csv
        resumen_pipeline.json
        comparacion_oficial.csv
        comparacion_oficial.json
        <instancia>/
            iter_00/
            iter_01/
            fase3_seed_1/
            fase3_seed_2/
            ...
            solucion_final_<instancia>.json
"""

from pathlib import Path
import argparse
import csv
import importlib.util
import json
import math
import re
import shutil
import time


# =============================================================================
# MEJORES COSTES OFICIALES PUBLICADOS PARA i01--i30
# =============================================================================
# Si actualizas estos valores, el resto del script no cambia.
# Para instancias que no estén en este diccionario, el gap se deja vacío.

COSTES_OFICIALES = {
    "i01": 3842,
    "i02": 1264,
    "i03": 10490,
    "i04": 1884,
    "i05": 12760,
    "i06": 10671,
    "i07": 5026,
    "i08": 6291,
    "i09": 6682,
    "i10": 20820,
    "i11": 25938,
    "i12": 12430,
    "i13": 17328,
    "i14": 9746,
    "i15": 12486,
    "i16": 10139,
    "i17": 40535,
    "i18": 37660,
    "i19": 44587,
    "i20": 29098,
    "i21": 24703,
    "i22": 47861,
    "i23": 37550,
    "i24": 33221,
    "i25": 11517,
    "i26": 64613,
    "i27": 51828,
    "i28": 75172,
    "i29": 12475,
    "i30": 37943,
}


# =============================================================================
# UTILIDADES GENERALES
# =============================================================================

def as_path(path):
    if path is None:
        return None
    return Path(path)


def load_json(path):
    path = Path(path)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(data, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


def write_csv(rows, path, columns):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def import_module_from_path(name, path):
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"No se encuentra el script requerido: {path}")

    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_seeds(text):
    """
    Acepta:
        "1-5"     -> [1,2,3,4,5]
        "1,3,8"   -> [1,3,8]
        "8"       -> [8]
    """
    text = str(text).strip()
    if not text:
        return [1]

    if "-" in text and "," not in text:
        a, b = text.split("-", 1)
        return list(range(int(a), int(b) + 1))

    return [int(x.strip()) for x in text.split(",") if x.strip()]


def resolve_instance_paths(args):
    base_dir = Path(args.base_dir).resolve()
    paths = []

    if args.instances:
        for item in args.instances.split(","):
            item = item.strip()
            if not item:
                continue
            p = Path(item)
            if not p.is_absolute():
                p = base_dir / p
            paths.append(p)

    if args.instances_dir:
        d = Path(args.instances_dir)
        if not d.is_absolute():
            d = base_dir / d
        paths.extend(sorted(d.glob(args.glob)))

    # Quitar duplicados preservando orden.
    out = []
    seen = set()
    for p in paths:
        rp = str(p.resolve())
        if rp not in seen:
            seen.add(rp)
            out.append(p)

    if not out:
        raise ValueError("No se ha indicado ninguna instancia. Usa --instances o --instances-dir.")

    return out


# =============================================================================
# VALIDADOR OFICIAL
# =============================================================================

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


def run_validator(validator, instance, solution, timeout=300):
    """
    Ejecuta el validador oficial.
    Si el validador no existe o la solución no existe, devuelve métricas None.
    """
    import subprocess

    validator = Path(validator)
    instance = Path(instance)
    solution = Path(solution)

    if not validator.exists():
        return {
            "ok": False,
            "returncode": None,
            "stdout": "",
            "stderr": f"No existe el validador: {validator}",
            "parsed": {"total_violations": None, "total_cost": None, "violations": {}, "costs": {}},
        }

    if not solution.exists():
        return {
            "ok": False,
            "returncode": None,
            "stdout": "",
            "stderr": f"No existe la solución: {solution}",
            "parsed": {"total_violations": None, "total_cost": None, "violations": {}, "costs": {}},
        }

    cmd = [str(validator), str(instance), str(solution)]

    res = subprocess.run(
        cmd,
        cwd=str(instance.parent),
        capture_output=True,
        text=True,
        shell=False,
        timeout=timeout,
    )

    text = (res.stdout or "") + "\n" + (res.stderr or "")

    return {
        "ok": res.returncode == 0,
        "returncode": res.returncode,
        "stdout": res.stdout,
        "stderr": res.stderr,
        "parsed": parse_validator_output(text),
    }


def cost(parsed, name):
    return parsed.get("costs", {}).get(name, {}).get("weighted_cost")


def raw_cost(parsed, name):
    return parsed.get("costs", {}).get(name, {}).get("raw_cost")


def viol(parsed, name):
    return parsed.get("violations", {}).get(name)


# =============================================================================
# FEEDBACK ENTRE FASES
# =============================================================================

def feedback_has_content(path):
    """
    Comprueba si fase 2 ha generado feedback útil para reejecutar fase 1.
    """
    path = Path(path)
    if not path.exists():
        return False

    try:
        data = load_json(path)
    except Exception:
        return False

    keys = [
        "day_penalties",
        "gender_day_penalties",
        "day_admission_caps",
        "gender_day_admission_caps",
    ]

    for k in keys:
        v = data.get(k, {})
        if isinstance(v, dict) and len(v) > 0:
            return True

    return False


# =============================================================================
# EJECUCIÓN DE FASE 1 Y FASE 2
# =============================================================================

def run_phase1(phase1, instance_path, output_path, feedback_path, stats_path, args):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if stats_path:
        Path(stats_path).parent.mkdir(parents=True, exist_ok=True)

    instancia = phase1.cargar_instancia(str(instance_path))

    kwargs = {
        "instancia": instancia,
        "ruta_salida": str(output_path),
        "ruta_feedback": str(feedback_path) if feedback_path is not None else None,
        "time_limit": args.time_fase1,
        "mip_gap": args.mip_gap_fase1,
        "usar_capacidad_genero": not args.sin_capacidad_genero,
        "usar_hall_compatibilidad": not args.sin_hall_habitaciones,
        "usar_hall_quirofanos": not args.sin_hall_quirofanos,
        "feedback_caps_suaves": not args.feedback_duro,
        "priorizar_opcionales": True,
        "salida_estadisticas": str(stats_path) if stats_path else None,
    }

    return phase1.resolver_fase1_scp_interactiva(**kwargs)


def run_phase2(phase2, instance_path, sol_phase1, sol_phase2, feedback_out, args):
    sol_phase2 = Path(sol_phase2)
    feedback_out = Path(feedback_out)
    sol_phase2.parent.mkdir(parents=True, exist_ok=True)
    feedback_out.parent.mkdir(parents=True, exist_ok=True)

    return phase2.resolver_habitaciones_debug(
        ruta_instancia=str(instance_path),
        ruta_sol_previa=str(sol_phase1),
        ruta_final=str(sol_phase2),
        ruta_feedback=str(feedback_out),
        time_limit=args.time_fase2,
    )


def choose_best_phase2(iter_rows):
    """
    Como fase 2 aún no incluye enfermería, puede tener UncoveredRoom.
    Seleccionamos una solución de fase 2 existente; si hay varias con coste,
    elegimos la de menor coste total del validador.
    """
    candidates = [
        r for r in iter_rows
        if r.get("phase2_exists") and r.get("phase2_total_cost") is not None
    ]

    if candidates:
        return min(candidates, key=lambda r: r["phase2_total_cost"])

    candidates = [r for r in iter_rows if r.get("phase2_exists")]
    if candidates:
        return candidates[-1]

    return None


# =============================================================================
# FASE 3 CON VARIAS SEMILLAS
# =============================================================================

def run_phase3_seed(phase3, instance_path, sol_phase2, out_dir, seed, args):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    sol_final = out_dir / f"solucion_fase3_seed_{seed}.json"
    stats = out_dir / f"estadisticas_fase3_seed_{seed}.json"

    print(f"[FASE 3] seed={seed} salida={sol_final}")

    start = time.time()

    try:
        phase3.resolver_enfermeras_sa(
            ruta_instancia=str(instance_path),
            ruta_sol_fase2=str(sol_phase2),
            ruta_salida=str(sol_final),
            ruta_estadisticas=str(stats),
            ruta_validador=str(args.validator),
            ruta_greedy_script=str(args.greedy_script),
            ruta_local_search_script=str(args.local_search_script),
            sa_max_iter=args.sa_max_iter,
            sa_time_limit=args.sa_time_limit,
            sa_T0=args.sa_T0,
            sa_alpha=args.sa_alpha,
            sa_Tmin=args.sa_Tmin,
            sa_candidates_per_iter=args.sa_candidates_per_iter,
            ls_final_time=args.ls_final_time,
            ls_final_max_iter=args.ls_final_max_iter,
            ls_final_max_no_improve=args.ls_final_max_no_improve,
            ls_final_candidates_per_iter=args.ls_final_candidates_per_iter,
            seed=seed,
            validar=True,
        )
        error = ""
    except Exception as e:
        error = repr(e)
        print(f"[ERROR FASE 3 seed={seed}] {error}")

    elapsed = time.time() - start

    val = run_validator(args.validator, instance_path, sol_final)
    save_json(val, out_dir / f"validator_seed_{seed}.json")
    parsed = val["parsed"]

    return {
        "seed": seed,
        "solution": str(sol_final),
        "stats": str(stats),
        "elapsed_seconds": round(elapsed, 3),
        "error": error,
        "total_violations": parsed.get("total_violations"),
        "total_cost": parsed.get("total_cost"),
        "RoomGenderMix": viol(parsed, "RoomGenderMix"),
        "PatientRoomCompatibility": viol(parsed, "PatientRoomCompatibility"),
        "SurgeonOvertime": viol(parsed, "SurgeonOvertime"),
        "OperatingTheaterOvertime": viol(parsed, "OperatingTheaterOvertime"),
        "MandatoryUnscheduledPatients": viol(parsed, "MandatoryUnscheduledPatients"),
        "AdmissionDay": viol(parsed, "AdmissionDay"),
        "RoomCapacity": viol(parsed, "RoomCapacity"),
        "NursePresence": viol(parsed, "NursePresence"),
        "UncoveredRoom": viol(parsed, "UncoveredRoom"),
        "cost_RoomAgeMix": cost(parsed, "RoomAgeMix"),
        "cost_RoomSkillLevel": cost(parsed, "RoomSkillLevel"),
        "cost_ContinuityOfCare": cost(parsed, "ContinuityOfCare"),
        "cost_ExcessiveNurseWorkload": cost(parsed, "ExcessiveNurseWorkload"),
        "cost_OpenOperatingTheater": cost(parsed, "OpenOperatingTheater"),
        "cost_SurgeonTransfer": cost(parsed, "SurgeonTransfer"),
        "cost_PatientDelay": cost(parsed, "PatientDelay"),
        "cost_ElectiveUnscheduledPatients": cost(parsed, "ElectiveUnscheduledPatients"),
    }


def choose_best_final(seed_rows):
    """
    Elige la mejor solución final:
        1) factible: total_violations == 0
        2) menor total_cost
    Si ninguna es factible, devuelve la de menos violaciones y menor coste.
    """
    feasible = [
        r for r in seed_rows
        if r.get("total_violations") == 0 and r.get("total_cost") is not None
    ]

    if feasible:
        return min(feasible, key=lambda r: r["total_cost"])

    candidates = [
        r for r in seed_rows
        if r.get("total_violations") is not None and r.get("total_cost") is not None
    ]

    if candidates:
        return min(candidates, key=lambda r: (r["total_violations"], r["total_cost"]))

    return None


# =============================================================================
# EJECUCIÓN DE UNA INSTANCIA
# =============================================================================

def run_instance(instance_path, phase1, phase2, phase3, args):
    instance_path = Path(instance_path)
    instance_name = instance_path.stem
    out_dir = Path(args.out_dir) / instance_name
    out_dir.mkdir(parents=True, exist_ok=True)

    print("\n" + "=" * 100)
    print(f"INSTANCIA {instance_path.name}")
    print("=" * 100)

    iter_rows = []
    feedback_in = None

    for it in range(args.feedback_iters):
        iter_dir = out_dir / f"iter_{it:02d}"
        iter_dir.mkdir(parents=True, exist_ok=True)

        sol_phase1 = iter_dir / "solucion_fase1.json"
        sol_phase2 = iter_dir / "solucion_fase2.json"
        feedback_out = iter_dir / "feedback_fase2.json"
        stats_phase1 = iter_dir / "estadisticas_fase1.json"

        print(f"\n[ITER {it:02d}] Fase 1")
        start = time.time()
        try:
            run_phase1(phase1, instance_path, sol_phase1, feedback_in, stats_phase1, args)
            phase1_error = ""
        except Exception as e:
            phase1_error = repr(e)
            print("[ERROR FASE 1]", phase1_error)
        t1 = time.time() - start

        print(f"\n[ITER {it:02d}] Fase 2")
        start = time.time()
        try:
            if sol_phase1.exists():
                run_phase2(phase2, instance_path, sol_phase1, sol_phase2, feedback_out, args)
                phase2_error = ""
            else:
                phase2_error = "No existe solucion_fase1.json"
                print("[ERROR FASE 2]", phase2_error)
        except Exception as e:
            phase2_error = repr(e)
            print("[ERROR FASE 2]", phase2_error)
        t2 = time.time() - start

        val2 = run_validator(args.validator, instance_path, sol_phase2)
        save_json(val2, iter_dir / "validator_fase2.json")
        parsed2 = val2["parsed"]

        row = {
            "instance": instance_path.name,
            "iter": it,
            "phase1_exists": sol_phase1.exists(),
            "phase2_exists": sol_phase2.exists(),
            "feedback_exists": feedback_out.exists(),
            "feedback_has_content": feedback_has_content(feedback_out),
            "phase1_error": phase1_error,
            "phase2_error": phase2_error,
            "time_phase1": round(t1, 3),
            "time_phase2": round(t2, 3),
            "sol_phase1": str(sol_phase1),
            "sol_phase2": str(sol_phase2),
            "feedback": str(feedback_out),
            "phase2_total_violations": parsed2.get("total_violations"),
            "phase2_total_cost": parsed2.get("total_cost"),
            "phase2_UncoveredRoom": viol(parsed2, "UncoveredRoom"),
            "phase2_RoomCapacity": viol(parsed2, "RoomCapacity"),
            "phase2_RoomAgeMix": cost(parsed2, "RoomAgeMix"),
            "phase2_OpenOperatingTheater": cost(parsed2, "OpenOperatingTheater"),
            "phase2_PatientDelay": cost(parsed2, "PatientDelay"),
            "phase2_ElectiveUnscheduledPatients": cost(parsed2, "ElectiveUnscheduledPatients"),
        }

        iter_rows.append(row)

        print("[ITER RESULT]")
        print("  phase2_exists:", row["phase2_exists"])
        print("  phase2_total_violations:", row["phase2_total_violations"])
        print("  phase2_total_cost:", row["phase2_total_cost"])
        print("  phase2_UncoveredRoom:", row["phase2_UncoveredRoom"])
        print("  feedback_has_content:", row["feedback_has_content"])

        # Feedback hacia la siguiente iteración.
        if row["feedback_has_content"]:
            feedback_in = feedback_out
        else:
            feedback_in = None
            if args.stop_if_no_feedback:
                print("[INFO] Sin feedback nuevo. Se detienen iteraciones F1-F2.")
                break

    save_json(iter_rows, out_dir / "iteraciones_fase12.json")
    write_csv(
        iter_rows,
        out_dir / "iteraciones_fase12.csv",
        [
            "instance", "iter", "phase1_exists", "phase2_exists",
            "feedback_exists", "feedback_has_content",
            "phase2_total_violations", "phase2_total_cost",
            "phase2_UncoveredRoom", "phase2_RoomCapacity",
            "phase2_RoomAgeMix", "phase2_OpenOperatingTheater",
            "phase2_PatientDelay", "phase2_ElectiveUnscheduledPatients",
            "time_phase1", "time_phase2",
            "sol_phase1", "sol_phase2", "feedback",
            "phase1_error", "phase2_error",
        ],
    )

    best_phase2 = choose_best_phase2(iter_rows)

    if best_phase2 is None:
        print("[WARNING] No se obtuvo ninguna solución de fase 2. No se ejecuta fase 3.")
        return {
            "instance": instance_path.name,
            "status": "sin_fase2",
            "final_total_violations": None,
            "final_total_cost": None,
            "official_cost": COSTES_OFICIALES.get(instance_name),
            "gap_absolute": None,
            "gap_relative_percent": None,
            "best_seed": None,
            "final_solution": None,
            "out_dir": str(out_dir),
        }

    best_phase2_path = Path(best_phase2["sol_phase2"])
    best_phase2_copy = out_dir / f"mejor_solucion_fase2_{instance_name}.json"
    shutil.copy(best_phase2_path, best_phase2_copy)

    # Fase 3 con semillas.
    seeds = parse_seeds(args.seeds)
    seed_rows = []

    for seed in seeds:
        seed_dir = out_dir / f"fase3_seed_{seed}"
        seed_row = run_phase3_seed(phase3, instance_path, best_phase2_copy, seed_dir, seed, args)
        seed_rows.append(seed_row)

    save_json(seed_rows, out_dir / "resultados_semillas_fase3.json")
    write_csv(
        seed_rows,
        out_dir / "resultados_semillas_fase3.csv",
        [
            "seed", "total_violations", "total_cost",
            "RoomGenderMix", "PatientRoomCompatibility", "SurgeonOvertime",
            "OperatingTheaterOvertime", "MandatoryUnscheduledPatients", "AdmissionDay",
            "RoomCapacity", "NursePresence", "UncoveredRoom",
            "cost_RoomAgeMix", "cost_RoomSkillLevel", "cost_ContinuityOfCare",
            "cost_ExcessiveNurseWorkload", "cost_OpenOperatingTheater",
            "cost_SurgeonTransfer", "cost_PatientDelay", "cost_ElectiveUnscheduledPatients",
            "elapsed_seconds", "solution", "stats", "error",
        ],
    )

    best_final = choose_best_final(seed_rows)

    if best_final is None:
        print("[WARNING] No se obtuvo ninguna solución final validable.")
        return {
            "instance": instance_path.name,
            "status": "sin_fase3",
            "final_total_violations": None,
            "final_total_cost": None,
            "official_cost": COSTES_OFICIALES.get(instance_name),
            "gap_absolute": None,
            "gap_relative_percent": None,
            "best_seed": None,
            "final_solution": None,
            "out_dir": str(out_dir),
        }

    final_solution = out_dir / f"solucion_final_{instance_name}.json"
    shutil.copy(best_final["solution"], final_solution)

    official_cost = COSTES_OFICIALES.get(instance_name)
    final_cost = best_final.get("total_cost")
    final_violations = best_final.get("total_violations")

    if official_cost is not None and final_cost is not None and final_violations == 0:
        gap_absolute = final_cost - official_cost
        gap_relative_percent = 100.0 * (final_cost - official_cost) / official_cost
    else:
        gap_absolute = None
        gap_relative_percent = None

    summary = {
        "instance": instance_path.name,
        "status": "ok",
        "best_phase2_iter": best_phase2.get("iter"),
        "best_phase2_solution": str(best_phase2_copy),
        "phase2_total_violations": best_phase2.get("phase2_total_violations"),
        "phase2_total_cost": best_phase2.get("phase2_total_cost"),
        "phase2_UncoveredRoom": best_phase2.get("phase2_UncoveredRoom"),
        "best_seed": best_final.get("seed"),
        "final_solution": str(final_solution),
        "final_total_violations": final_violations,
        "final_total_cost": final_cost,
        "official_cost": official_cost,
        "gap_absolute": gap_absolute,
        "gap_relative_percent": gap_relative_percent,
        "final_RoomGenderMix": best_final.get("RoomGenderMix"),
        "final_PatientRoomCompatibility": best_final.get("PatientRoomCompatibility"),
        "final_SurgeonOvertime": best_final.get("SurgeonOvertime"),
        "final_OperatingTheaterOvertime": best_final.get("OperatingTheaterOvertime"),
        "final_MandatoryUnscheduledPatients": best_final.get("MandatoryUnscheduledPatients"),
        "final_AdmissionDay": best_final.get("AdmissionDay"),
        "final_RoomCapacity": best_final.get("RoomCapacity"),
        "final_NursePresence": best_final.get("NursePresence"),
        "final_UncoveredRoom": best_final.get("UncoveredRoom"),
        "cost_RoomAgeMix": best_final.get("cost_RoomAgeMix"),
        "cost_RoomSkillLevel": best_final.get("cost_RoomSkillLevel"),
        "cost_ContinuityOfCare": best_final.get("cost_ContinuityOfCare"),
        "cost_ExcessiveNurseWorkload": best_final.get("cost_ExcessiveNurseWorkload"),
        "cost_OpenOperatingTheater": best_final.get("cost_OpenOperatingTheater"),
        "cost_SurgeonTransfer": best_final.get("cost_SurgeonTransfer"),
        "cost_PatientDelay": best_final.get("cost_PatientDelay"),
        "cost_ElectiveUnscheduledPatients": best_final.get("cost_ElectiveUnscheduledPatients"),
        "out_dir": str(out_dir),
    }

    save_json(summary, out_dir / "resumen_instancia.json")

    print("\n[RESUMEN INSTANCIA]")
    print("  final_total_violations:", summary["final_total_violations"])
    print("  final_total_cost:", summary["final_total_cost"])
    print("  official_cost:", summary["official_cost"])
    print("  gap_relative_percent:", summary["gap_relative_percent"])
    print("  best_seed:", summary["best_seed"])
    print("  final_solution:", summary["final_solution"])

    return summary


# =============================================================================
# MAIN
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Ejecuta todas las fases IHTC, valida soluciones y compara con costes oficiales."
    )

    parser.add_argument("--base-dir", default=".", help="Carpeta base del proyecto.")
    parser.add_argument("--src-dir", default=None, help="Carpeta donde están los scripts. Por defecto, base-dir.")
    parser.add_argument("--instances", default=None, help="Lista separada por comas de instancias JSON.")
    parser.add_argument("--instances-dir", default=None, help="Carpeta con instancias JSON.")
    parser.add_argument("--glob", default="i*.json", help="Patrón si se usa --instances-dir.")

    parser.add_argument("--out-dir", default="resultados_experimentos_ihtc", help="Carpeta de salida.")
    parser.add_argument("--validator", default="IHTP_Validator.exe", help="Ruta del validador oficial.")

    parser.add_argument("--phase1-script", default=None, help="Ruta de modelo_scp_gurobi.py.")
    parser.add_argument("--phase2-script", default=None, help="Ruta de modelo_habitaciones_gurobi.py.")
    parser.add_argument("--phase3-script", default=None, help="Ruta de modelo_enfermeras_sa.py.")
    parser.add_argument("--greedy-script", default=None, help="Ruta de modelo_enfermeras_greedy_inicio.py.")
    parser.add_argument("--local-search-script", default=None, help="Ruta de modelo_enfermeras_busqueda_local.py.")

    # Feedback F1-F2.
    parser.add_argument("--feedback-iters", type=int, default=2, help="Número máximo de iteraciones fase 1-2 con feedback.")
    parser.add_argument("--stop-if-no-feedback", action="store_true", help="Detiene F1-F2 si fase 2 no genera feedback.")

    # Parámetros fase 1 y 2.
    parser.add_argument("--time-fase1", type=float, default=120, help="Time limit de Gurobi para fase 1.")
    parser.add_argument("--mip-gap-fase1", type=float, default=0.02, help="MIPGap fase 1.")
    parser.add_argument("--time-fase2", type=float, default=60, help="Time limit para fase 2.")
    parser.add_argument("--feedback-duro", action="store_true", help="Usa feedback como límite duro en fase 1.")
    parser.add_argument("--sin-capacidad-genero", action="store_true", help="Desactiva anticipación de capacidad por género.")
    parser.add_argument("--sin-hall-habitaciones", action="store_true", help="Desactiva Hall de habitaciones en fase 1.")
    parser.add_argument("--sin-hall-quirofanos", action="store_true", help="Desactiva Hall de quirófanos en fase 1.")

    # Fase 3.
    parser.add_argument("--seeds", default="1-3", help="Semillas de SA. Ejemplo: 1-3 o 1,2,8.")
    parser.add_argument("--sa-time-limit", type=float, default=180, help="Tiempo máximo de SA por semilla.")
    parser.add_argument("--sa-max-iter", type=int, default=60000, help="Iteraciones máximas de SA.")
    parser.add_argument("--sa-T0", type=float, default=50.0, help="Temperatura inicial SA.")
    parser.add_argument("--sa-alpha", type=float, default=0.9995, help="Enfriamiento SA.")
    parser.add_argument("--sa-Tmin", type=float, default=1e-4, help="Temperatura mínima SA.")
    parser.add_argument("--sa-candidates-per-iter", type=int, default=1, help="Vecinos por iteración SA.")
    parser.add_argument("--ls-final-time", type=float, default=60, help="Tiempo búsqueda local final.")
    parser.add_argument("--ls-final-max-iter", type=int, default=15000, help="Iteraciones máximas búsqueda local final.")
    parser.add_argument("--ls-final-max-no-improve", type=int, default=3000, help="Iteraciones sin mejora búsqueda local final.")
    parser.add_argument("--ls-final-candidates-per-iter", type=int, default=50, help="Vecinos por iteración búsqueda local final.")

    args = parser.parse_args()

    args.base_dir = Path(args.base_dir).resolve()
    args.src_dir = Path(args.src_dir).resolve() if args.src_dir else args.base_dir

    args.out_dir = Path(args.out_dir)
    if not args.out_dir.is_absolute():
        args.out_dir = args.base_dir / args.out_dir
    args.out_dir.mkdir(parents=True, exist_ok=True)

    args.validator = Path(args.validator)
    if not args.validator.is_absolute():
        args.validator = args.base_dir / args.validator

    # Scripts.
    args.phase1_script = Path(args.phase1_script) if args.phase1_script else args.src_dir / "modelo_scp_gurobi.py"
    args.phase2_script = Path(args.phase2_script) if args.phase2_script else args.src_dir / "modelo_habitaciones_gurobi.py"
    args.phase3_script = Path(args.phase3_script) if args.phase3_script else args.src_dir / "modelo_enfermeras_sa.py"
    args.greedy_script = Path(args.greedy_script) if args.greedy_script else args.src_dir / "modelo_enfermeras_greedy_inicio.py"
    args.local_search_script = Path(args.local_search_script) if args.local_search_script else args.src_dir / "modelo_enfermeras_busqueda_local.py"

    if not args.phase1_script.is_absolute():
        args.phase1_script = args.base_dir / args.phase1_script
    if not args.phase2_script.is_absolute():
        args.phase2_script = args.base_dir / args.phase2_script
    if not args.phase3_script.is_absolute():
        args.phase3_script = args.base_dir / args.phase3_script
    if not args.greedy_script.is_absolute():
        args.greedy_script = args.base_dir / args.greedy_script
    if not args.local_search_script.is_absolute():
        args.local_search_script = args.base_dir / args.local_search_script

    print("=" * 100)
    print("EJECUCIÓN COMPLETA IHTC")
    print("=" * 100)
    print("[INFO] base_dir:", args.base_dir)
    print("[INFO] src_dir:", args.src_dir)
    print("[INFO] out_dir:", args.out_dir)
    print("[INFO] validator:", args.validator)
    print("[INFO] phase1:", args.phase1_script)
    print("[INFO] phase2:", args.phase2_script)
    print("[INFO] phase3:", args.phase3_script)
    print("[INFO] greedy:", args.greedy_script)
    print("[INFO] local search:", args.local_search_script)

    instances = resolve_instance_paths(args)
    print("[INFO] instancias:", [str(p) for p in instances])
    print("[INFO] seeds:", parse_seeds(args.seeds))

    # Importar módulos.
    phase1 = import_module_from_path("fase1_model", args.phase1_script)
    phase2 = import_module_from_path("fase2_model", args.phase2_script)
    phase3 = import_module_from_path("fase3_sa_model", args.phase3_script)

    summaries = []

    for instance in instances:
        summary = run_instance(instance, phase1, phase2, phase3, args)
        summaries.append(summary)

    # Guardar resumen general.
    summary_cols = [
        "instance", "status",
        "best_phase2_iter", "phase2_total_violations", "phase2_total_cost", "phase2_UncoveredRoom",
        "best_seed", "final_total_violations", "final_total_cost",
        "official_cost", "gap_absolute", "gap_relative_percent",
        "final_RoomGenderMix", "final_PatientRoomCompatibility", "final_SurgeonOvertime",
        "final_OperatingTheaterOvertime", "final_MandatoryUnscheduledPatients", "final_AdmissionDay",
        "final_RoomCapacity", "final_NursePresence", "final_UncoveredRoom",
        "cost_RoomAgeMix", "cost_RoomSkillLevel", "cost_ContinuityOfCare",
        "cost_ExcessiveNurseWorkload", "cost_OpenOperatingTheater", "cost_SurgeonTransfer",
        "cost_PatientDelay", "cost_ElectiveUnscheduledPatients",
        "final_solution", "best_phase2_solution", "out_dir",
    ]

    save_json(summaries, args.out_dir / "resumen_pipeline.json")
    write_csv(summaries, args.out_dir / "resumen_pipeline.csv", summary_cols)

    comparable = [
        r for r in summaries
        if r.get("status") == "ok"
        and r.get("final_total_violations") == 0
        and r.get("official_cost") is not None
        and r.get("final_total_cost") is not None
    ]

    save_json(comparable, args.out_dir / "comparacion_oficial.json")
    write_csv(
        comparable,
        args.out_dir / "comparacion_oficial.csv",
        [
            "instance", "official_cost", "final_total_cost",
            "final_total_violations", "gap_absolute", "gap_relative_percent",
            "best_seed",
            "cost_RoomAgeMix", "cost_RoomSkillLevel", "cost_ContinuityOfCare",
            "cost_ExcessiveNurseWorkload", "cost_OpenOperatingTheater",
            "cost_SurgeonTransfer", "cost_PatientDelay", "cost_ElectiveUnscheduledPatients",
            "final_solution",
        ],
    )

    print("\n" + "=" * 100)
    print("RESUMEN FINAL")
    print("=" * 100)
    print(f"Instancias procesadas: {len(summaries)}")
    print(f"Instancias comparables factibles: {len(comparable)}")

    if comparable:
        gaps = [r["gap_relative_percent"] for r in comparable if r.get("gap_relative_percent") is not None]
        if gaps:
            avg = sum(gaps) / len(gaps)
            best = min(comparable, key=lambda r: r["gap_relative_percent"])
            worst = max(comparable, key=lambda r: r["gap_relative_percent"])
            print(f"Gap medio: {avg:.2f}%")
            print(f"Mejor gap: {best['instance']} = {best['gap_relative_percent']:.2f}%")
            print(f"Peor gap: {worst['instance']} = {worst['gap_relative_percent']:.2f}%")

    print("[OUT] resumen:", args.out_dir / "resumen_pipeline.csv")
    print("[OUT] comparación:", args.out_dir / "comparacion_oficial.csv")


if __name__ == "__main__":
    main()
