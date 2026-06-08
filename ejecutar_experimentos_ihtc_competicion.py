"""
ejecutar_experimentos_ihtc_competicion.py

Orquestador para evaluar la metodología bajo un presupuesto comparable a IHTC 2024:
    - 600 segundos por instancia y ejecución independiente.
    - 4 hilos como máximo para modelos Gurobi.
    - 1 semilla por ejecución.
    - Validación con IHTP_Validator.exe.
    - Comparación con costes oficiales i01--i30.

Uso recomendado, una ejecución comparable:
    py ejecutar_experimentos_ihtc_competicion.py --instances-dir instancias --glob "i*.json" --runs 1

Evaluación tipo final, varias ejecuciones independientes, cada una con 600 s:
    py ejecutar_experimentos_ihtc_competicion.py --instances-dir instancias --glob "i*.json" --runs 10

Si los scripts están en src/:
    py ejecutar_experimentos_ihtc_competicion.py --src-dir src --instances-dir data --glob "i*.json" --runs 1
"""

from pathlib import Path
import argparse
import csv
import importlib.util
import json
import re
import shutil
import subprocess
import time


COSTES_OFICIALES = {
    "i01": 3842, "i02": 1264, "i03": 10490, "i04": 1884, "i05": 12760,
    "i06": 10671, "i07": 5026, "i08": 6291, "i09": 6682, "i10": 20820,
    "i11": 25938, "i12": 12430, "i13": 17328, "i14": 9746, "i15": 12486,
    "i16": 10139, "i17": 40535, "i18": 37660, "i19": 44587, "i20": 29098,
    "i21": 24703, "i22": 47861, "i23": 37550, "i24": 33221, "i25": 11517,
    "i26": 64613, "i27": 51828, "i28": 75172, "i29": 12475, "i30": 37943,
}


def load_json(path):
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


def import_module(name, path):
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"No se encuentra el script requerido: {path}")
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def resolve_paths(args):
    base = Path(args.base_dir).resolve()
    paths = []
    if args.instances:
        for item in args.instances.split(","):
            item = item.strip()
            if item:
                p = Path(item)
                paths.append(p if p.is_absolute() else base / p)
    if args.instances_dir:
        d = Path(args.instances_dir)
        d = d if d.is_absolute() else base / d
        paths.extend(sorted(d.glob(args.glob)))

    seen, out = set(), []
    for p in paths:
        key = str(p.resolve())
        if key not in seen:
            seen.add(key)
            out.append(p)
    if not out:
        raise ValueError("No se ha indicado ninguna instancia.")
    return out


def seconds_left(deadline):
    return max(0.0, deadline - time.time())


def capped_time(requested, deadline, reserve=0.0, minimum=1.0):
    available = seconds_left(deadline) - reserve
    if available < minimum:
        return 0.0
    return min(float(requested), available)


def patch_gurobi_threads(threads):
    """Fija Threads=threads para cada nuevo gp.Model() sin editar los modelos."""
    try:
        import gurobipy as gp
    except Exception as exc:
        print("[WARNING] No se pudo importar gurobipy para fijar Threads:", repr(exc))
        return

    if getattr(gp, "_ihtc_competition_threads_patch", False):
        return

    original_model = gp.Model

    def model_with_threads(*args, **kwargs):
        m = original_model(*args, **kwargs)
        try:
            m.setParam("Threads", int(threads))
        except Exception as exc:
            print("[WARNING] No se pudo fijar Threads:", repr(exc))
        return m

    gp.Model = model_with_threads
    gp._ihtc_competition_threads_patch = True
    print(f"[INFO] Gurobi Threads fijado a {threads}.")


def parse_validator(text):
    violations, costs = {}, {}
    total_violations, total_cost = None, None
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
        elif section == "costs":
            m = re.match(r"([A-Za-z]+)\.*\s*([0-9]+)\s*\(\s*([0-9]+)\s*X\s*([0-9]+)\s*\)", line)
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


def validate(validator, instance, solution, timeout=120):
    validator, instance, solution = Path(validator), Path(instance), Path(solution)
    if not validator.exists():
        return {"ok": False, "stderr": f"No existe el validador: {validator}", "parsed": {"total_violations": None, "total_cost": None, "violations": {}, "costs": {}}}
    if not solution.exists():
        return {"ok": False, "stderr": f"No existe la solución: {solution}", "parsed": {"total_violations": None, "total_cost": None, "violations": {}, "costs": {}}}
    res = subprocess.run([str(validator), str(instance), str(solution)], cwd=str(instance.parent), capture_output=True, text=True, timeout=timeout)
    text = (res.stdout or "") + "\n" + (res.stderr or "")
    return {"ok": res.returncode == 0, "returncode": res.returncode, "stdout": res.stdout, "stderr": res.stderr, "parsed": parse_validator(text)}


def v(parsed, name):
    return parsed.get("violations", {}).get(name)


def c(parsed, name):
    return parsed.get("costs", {}).get(name, {}).get("weighted_cost")


def feedback_has_content(path):
    path = Path(path)
    if not path.exists():
        return False
    try:
        data = load_json(path)
    except Exception:
        return False
    for key in ["day_penalties", "gender_day_penalties", "day_admission_caps", "gender_day_admission_caps"]:
        value = data.get(key, {})
        if isinstance(value, dict) and value:
            return True
    return False


def run_phase1(phase1, instance, output, feedback, stats, args, time_limit):
    output, stats = Path(output), Path(stats)
    output.parent.mkdir(parents=True, exist_ok=True)
    stats.parent.mkdir(parents=True, exist_ok=True)
    inst = phase1.cargar_instancia(str(instance))
    return phase1.resolver_fase1_scp_interactiva(
        inst,
        ruta_salida=str(output),
        ruta_feedback=str(feedback) if feedback is not None else None,
        time_limit=time_limit,
        mip_gap=args.mip_gap_fase1,
        usar_capacidad_genero=not args.sin_capacidad_genero,
        usar_hall_compatibilidad=not args.sin_hall_habitaciones,
        usar_hall_quirofanos=not args.sin_hall_quirofanos,
        feedback_caps_suaves=not args.feedback_duro,
        priorizar_opcionales=True,
        salida_estadisticas=str(stats),
    )


def run_phase2(phase2, instance, sol1, sol2, feedback, time_limit):
    sol2, feedback = Path(sol2), Path(feedback)
    sol2.parent.mkdir(parents=True, exist_ok=True)
    feedback.parent.mkdir(parents=True, exist_ok=True)
    return phase2.resolver_habitaciones_debug(
        ruta_instancia=str(instance),
        ruta_sol_previa=str(sol1),
        ruta_final=str(sol2),
        ruta_feedback=str(feedback),
        time_limit=time_limit,
    )


def run_phase3(phase3, instance, sol2, final, stats, args, seed, sa_time, ls_time):
    final, stats = Path(final), Path(stats)
    final.parent.mkdir(parents=True, exist_ok=True)
    stats.parent.mkdir(parents=True, exist_ok=True)
    return phase3.resolver_enfermeras_sa(
        ruta_instancia=str(instance),
        ruta_sol_fase2=str(sol2),
        ruta_salida=str(final),
        ruta_estadisticas=str(stats),
        ruta_validador=str(args.validator),
        ruta_greedy_script=str(args.greedy_script),
        ruta_local_search_script=str(args.local_search_script),
        sa_max_iter=args.sa_max_iter,
        sa_time_limit=sa_time,
        sa_T0=args.sa_T0,
        sa_alpha=args.sa_alpha,
        sa_Tmin=args.sa_Tmin,
        sa_candidates_per_iter=args.sa_candidates_per_iter,
        ls_final_time=ls_time,
        ls_final_max_iter=args.ls_final_max_iter,
        ls_final_max_no_improve=args.ls_final_max_no_improve,
        ls_final_candidates_per_iter=args.ls_final_candidates_per_iter,
        seed=seed,
        validar=True,
    )


def choose_phase2(rows):
    ok = [r for r in rows if r.get("phase2_exists") and r.get("phase2_total_cost") is not None]
    if ok:
        return min(ok, key=lambda r: r["phase2_total_cost"])
    ok = [r for r in rows if r.get("phase2_exists")]
    return ok[-1] if ok else None


def run_one(instance, run_id, seed, phase1, phase2, phase3, args):
    instance = Path(instance)
    name = instance.stem
    run_dir = Path(args.out_dir) / name / f"run_{run_id:02d}"
    run_dir.mkdir(parents=True, exist_ok=True)
    start = time.time()
    deadline = start + args.total_time_limit

    print("\n" + "=" * 90)
    print(f"{instance.name} | run {run_id} | seed {seed} | presupuesto {args.total_time_limit}s | threads {args.threads}")
    print("=" * 90)

    feedback_in = None
    iter_rows = []

    for it in range(args.feedback_iters):
        if seconds_left(deadline) <= args.min_time_for_phase3:
            print("[INFO] Se reserva el tiempo restante para fase 3; se paran iteraciones F1-F2.")
            break

        iter_dir = run_dir / f"iter_{it:02d}"
        sol1 = iter_dir / "solucion_fase1.json"
        sol2 = iter_dir / "solucion_fase2.json"
        fb = iter_dir / "feedback_fase2.json"
        stats1 = iter_dir / "estadisticas_fase1.json"

        tlim1 = capped_time(args.time_fase1, deadline, reserve=args.reserve_after_phase1, minimum=5)
        print(f"[F1 iter {it}] TimeLimit={tlim1:.1f}s | restante={seconds_left(deadline):.1f}s")
        t0 = time.time()
        err1 = ""
        try:
            if tlim1 <= 0:
                raise RuntimeError("No queda tiempo suficiente para fase 1")
            run_phase1(phase1, instance, sol1, feedback_in, stats1, args, tlim1)
        except Exception as exc:
            err1 = repr(exc)
            print("[ERROR F1]", err1)
        time1 = time.time() - t0

        tlim2 = capped_time(args.time_fase2, deadline, reserve=args.min_time_for_phase3, minimum=5)
        print(f"[F2 iter {it}] TimeLimit={tlim2:.1f}s | restante={seconds_left(deadline):.1f}s")
        t0 = time.time()
        err2 = ""
        try:
            if not sol1.exists():
                raise RuntimeError("No existe solucion_fase1.json")
            if tlim2 <= 0:
                raise RuntimeError("No queda tiempo suficiente para fase 2")
            run_phase2(phase2, instance, sol1, sol2, fb, tlim2)
        except Exception as exc:
            err2 = repr(exc)
            print("[ERROR F2]", err2)
        time2 = time.time() - t0

        val2 = validate(args.validator, instance, sol2, timeout=args.validator_timeout)
        save_json(val2, iter_dir / "validator_fase2.json")
        parsed2 = val2["parsed"]
        row = {
            "iter": it,
            "phase1_exists": sol1.exists(),
            "phase2_exists": sol2.exists(),
            "feedback_has_content": feedback_has_content(fb),
            "phase1_error": err1,
            "phase2_error": err2,
            "time_phase1": round(time1, 3),
            "time_phase2": round(time2, 3),
            "phase2_total_violations": parsed2.get("total_violations"),
            "phase2_total_cost": parsed2.get("total_cost"),
            "phase2_UncoveredRoom": v(parsed2, "UncoveredRoom"),
            "sol_phase1": str(sol1),
            "sol_phase2": str(sol2),
            "feedback": str(fb),
        }
        iter_rows.append(row)
        print("[F2 result] exists=", row["phase2_exists"], "viol=", row["phase2_total_violations"], "cost=", row["phase2_total_cost"], "feedback=", row["feedback_has_content"])

        if row["feedback_has_content"]:
            feedback_in = fb
        else:
            feedback_in = None
            if args.stop_if_no_feedback:
                break

    save_json(iter_rows, run_dir / "iteraciones_fase12.json")
    best2 = choose_phase2(iter_rows)
    if best2 is None:
        return build_summary(instance, run_id, seed, "sin_fase2", start, args, None, None, None, run_dir)

    best2_copy = run_dir / f"mejor_solucion_fase2_{name}.json"
    shutil.copy(best2["sol_phase2"], best2_copy)

    left_for_phase3 = max(0.0, seconds_left(deadline) - args.final_validation_margin)
    if left_for_phase3 < 5:
        return build_summary(instance, run_id, seed, "sin_tiempo_fase3", start, args, best2, None, None, run_dir)

    ls_time = min(args.ls_final_time, max(0.0, left_for_phase3 * args.ls_fraction))
    sa_time = max(1.0, left_for_phase3 - ls_time)
    final = run_dir / f"solucion_final_{name}.json"
    stats3 = run_dir / "estadisticas_fase3.json"
    print(f"[F3] SA={sa_time:.1f}s | LS={ls_time:.1f}s | restante={seconds_left(deadline):.1f}s")
    err3 = ""
    t0 = time.time()
    try:
        run_phase3(phase3, instance, best2_copy, final, stats3, args, seed, sa_time, ls_time)
    except Exception as exc:
        err3 = repr(exc)
        print("[ERROR F3]", err3)
    time3 = time.time() - t0

    val = validate(args.validator, instance, final, timeout=args.validator_timeout)
    save_json(val, run_dir / "validator_final.json")
    return build_summary(instance, run_id, seed, "ok" if final.exists() else "sin_fase3", start, args, best2, val, {"time_phase3": time3, "phase3_error": err3, "final_solution": str(final)}, run_dir)


def build_summary(instance, run_id, seed, status, start, args, best2, val, extra, run_dir):
    instance = Path(instance)
    name = instance.stem
    parsed = val["parsed"] if val else {"total_violations": None, "total_cost": None, "violations": {}, "costs": {}}
    official = COSTES_OFICIALES.get(name)
    total_cost = parsed.get("total_cost")
    total_viol = parsed.get("total_violations")
    if official is not None and total_cost is not None and total_viol == 0:
        gap_abs = total_cost - official
        gap_rel = 100.0 * gap_abs / official
    else:
        gap_abs = None
        gap_rel = None
    extra = extra or {}
    summary = {
        "instance": instance.name,
        "run_id": run_id,
        "seed": seed,
        "status": status,
        "budget_seconds": args.total_time_limit,
        "elapsed_seconds": round(time.time() - start, 3),
        "threads": args.threads,
        "best_phase2_iter": best2.get("iter") if best2 else None,
        "phase2_total_violations": best2.get("phase2_total_violations") if best2 else None,
        "phase2_total_cost": best2.get("phase2_total_cost") if best2 else None,
        "phase2_UncoveredRoom": best2.get("phase2_UncoveredRoom") if best2 else None,
        "time_phase3": round(extra.get("time_phase3", 0.0), 3) if extra else None,
        "phase3_error": extra.get("phase3_error"),
        "final_total_violations": total_viol,
        "final_total_cost": total_cost,
        "official_cost": official,
        "gap_absolute": gap_abs,
        "gap_relative_percent": gap_rel,
        "final_RoomGenderMix": v(parsed, "RoomGenderMix"),
        "final_PatientRoomCompatibility": v(parsed, "PatientRoomCompatibility"),
        "final_SurgeonOvertime": v(parsed, "SurgeonOvertime"),
        "final_OperatingTheaterOvertime": v(parsed, "OperatingTheaterOvertime"),
        "final_MandatoryUnscheduledPatients": v(parsed, "MandatoryUnscheduledPatients"),
        "final_AdmissionDay": v(parsed, "AdmissionDay"),
        "final_RoomCapacity": v(parsed, "RoomCapacity"),
        "final_NursePresence": v(parsed, "NursePresence"),
        "final_UncoveredRoom": v(parsed, "UncoveredRoom"),
        "cost_RoomAgeMix": c(parsed, "RoomAgeMix"),
        "cost_RoomSkillLevel": c(parsed, "RoomSkillLevel"),
        "cost_ContinuityOfCare": c(parsed, "ContinuityOfCare"),
        "cost_ExcessiveNurseWorkload": c(parsed, "ExcessiveNurseWorkload"),
        "cost_OpenOperatingTheater": c(parsed, "OpenOperatingTheater"),
        "cost_SurgeonTransfer": c(parsed, "SurgeonTransfer"),
        "cost_PatientDelay": c(parsed, "PatientDelay"),
        "cost_ElectiveUnscheduledPatients": c(parsed, "ElectiveUnscheduledPatients"),
        "final_solution": extra.get("final_solution") if extra else None,
        "out_dir": str(run_dir),
    }
    save_json(summary, Path(run_dir) / "resumen_run.json")
    print("[RUN summary] viol=", total_viol, "cost=", total_cost, "official=", official, "gap%=", gap_rel, "elapsed=", summary["elapsed_seconds"])
    return summary


def choose_best(runs):
    feasible = [r for r in runs if r.get("final_total_violations") == 0 and r.get("final_total_cost") is not None]
    if feasible:
        return min(feasible, key=lambda r: r["final_total_cost"])
    valid = [r for r in runs if r.get("final_total_violations") is not None and r.get("final_total_cost") is not None]
    return min(valid, key=lambda r: (r["final_total_violations"], r["final_total_cost"])) if valid else None


def main():
    parser = argparse.ArgumentParser(description="Ejecución comparable IHTC: 600 s por run y 4 hilos.")
    parser.add_argument("--base-dir", default=".")
    parser.add_argument("--src-dir", default=None)
    parser.add_argument("--instances", default=None)
    parser.add_argument("--instances-dir", default=None)
    parser.add_argument("--glob", default="i*.json")
    parser.add_argument("--out-dir", default="resultados_competicion_ihtc")
    parser.add_argument("--validator", default="IHTP_Validator.exe")
    parser.add_argument("--phase1-script", default=None)
    parser.add_argument("--phase2-script", default=None)
    parser.add_argument("--phase3-script", default=None)
    parser.add_argument("--greedy-script", default=None)
    parser.add_argument("--local-search-script", default=None)
    parser.add_argument("--total-time-limit", type=float, default=600.0)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--seed-start", type=int, default=1)
    parser.add_argument("--feedback-iters", type=int, default=1)
    parser.add_argument("--time-fase1", type=float, default=180.0)
    parser.add_argument("--time-fase2", type=float, default=90.0)
    parser.add_argument("--min-time-for-phase3", type=float, default=240.0)
    parser.add_argument("--reserve-after-phase1", type=float, default=330.0)
    parser.add_argument("--validator-timeout", type=float, default=120.0)
    parser.add_argument("--final-validation-margin", type=float, default=10.0)
    parser.add_argument("--mip-gap-fase1", type=float, default=0.02)
    parser.add_argument("--feedback-duro", action="store_true")
    parser.add_argument("--stop-if-no-feedback", action="store_true")
    parser.add_argument("--sin-capacidad-genero", action="store_true")
    parser.add_argument("--sin-hall-habitaciones", action="store_true")
    parser.add_argument("--sin-hall-quirofanos", action="store_true")
    parser.add_argument("--sa-max-iter", type=int, default=200000)
    parser.add_argument("--sa-T0", type=float, default=50.0)
    parser.add_argument("--sa-alpha", type=float, default=0.9995)
    parser.add_argument("--sa-Tmin", type=float, default=1e-4)
    parser.add_argument("--sa-candidates-per-iter", type=int, default=1)
    parser.add_argument("--ls-final-time", type=float, default=60.0)
    parser.add_argument("--ls-fraction", type=float, default=0.15)
    parser.add_argument("--ls-final-max-iter", type=int, default=200000)
    parser.add_argument("--ls-final-max-no-improve", type=int, default=5000)
    parser.add_argument("--ls-final-candidates-per-iter", type=int, default=50)
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

    args.phase1_script = Path(args.phase1_script) if args.phase1_script else args.src_dir / "modelo_scp_gurobi.py"
    args.phase2_script = Path(args.phase2_script) if args.phase2_script else args.src_dir / "modelo_habitaciones_gurobi.py"
    args.phase3_script = Path(args.phase3_script) if args.phase3_script else args.src_dir / "modelo_enfermeras_sa.py"
    args.greedy_script = Path(args.greedy_script) if args.greedy_script else args.src_dir / "modelo_enfermeras_greedy_inicio.py"
    args.local_search_script = Path(args.local_search_script) if args.local_search_script else args.src_dir / "modelo_enfermeras_busqueda_local.py"
    for attr in ["phase1_script", "phase2_script", "phase3_script", "greedy_script", "local_search_script"]:
        p = getattr(args, attr)
        if not p.is_absolute():
            setattr(args, attr, args.base_dir / p)

    print("=" * 90)
    print("EJECUCIÓN COMPARABLE IHTC")
    print("=" * 90)
    print("[INFO] total_time_limit:", args.total_time_limit)
    print("[INFO] threads:", args.threads)
    print("[INFO] runs:", args.runs)
    print("[INFO] out_dir:", args.out_dir)
    print("[INFO] validator:", args.validator)

    patch_gurobi_threads(args.threads)
    instances = resolve_paths(args)
    phase1 = import_module("fase1_model", args.phase1_script)
    phase2 = import_module("fase2_model", args.phase2_script)
    phase3 = import_module("fase3_model", args.phase3_script)

    all_runs = []
    best_rows = []
    for inst in instances:
        inst_runs = []
        for r in range(args.runs):
            seed = args.seed_start + r
            row = run_one(inst, r + 1, seed, phase1, phase2, phase3, args)
            inst_runs.append(row)
            all_runs.append(row)
        best = choose_best(inst_runs)
        if best is None:
            best = {"instance": Path(inst).name, "status": "sin_resultado", "official_cost": COSTES_OFICIALES.get(Path(inst).stem)}
        best_rows.append(best)

    cols = [
        "instance", "run_id", "seed", "status", "budget_seconds", "elapsed_seconds", "threads",
        "best_phase2_iter", "phase2_total_violations", "phase2_total_cost", "phase2_UncoveredRoom",
        "time_phase3", "final_total_violations", "final_total_cost", "official_cost",
        "gap_absolute", "gap_relative_percent", "final_RoomGenderMix", "final_PatientRoomCompatibility",
        "final_SurgeonOvertime", "final_OperatingTheaterOvertime", "final_MandatoryUnscheduledPatients",
        "final_AdmissionDay", "final_RoomCapacity", "final_NursePresence", "final_UncoveredRoom",
        "cost_RoomAgeMix", "cost_RoomSkillLevel", "cost_ContinuityOfCare", "cost_ExcessiveNurseWorkload",
        "cost_OpenOperatingTheater", "cost_SurgeonTransfer", "cost_PatientDelay", "cost_ElectiveUnscheduledPatients",
        "final_solution", "out_dir", "phase3_error"
    ]
    save_json(all_runs, args.out_dir / "resumen_runs.json")
    write_csv(all_runs, args.out_dir / "resumen_runs.csv", cols)
    save_json(best_rows, args.out_dir / "comparacion_oficial_competicion.json")
    write_csv(best_rows, args.out_dir / "comparacion_oficial_competicion.csv", cols)

    comparable = [r for r in best_rows if r.get("final_total_violations") == 0 and r.get("final_total_cost") is not None and r.get("official_cost") is not None]
    print("\n" + "=" * 90)
    print("RESUMEN FINAL")
    print("=" * 90)
    print("Instancias:", len(instances))
    print("Runs totales:", len(all_runs))
    print("Instancias comparables factibles:", len(comparable))
    if comparable:
        gaps = [r["gap_relative_percent"] for r in comparable if r.get("gap_relative_percent") is not None]
        if gaps:
            print(f"Gap medio: {sum(gaps)/len(gaps):.2f}%")
            best = min(comparable, key=lambda r: r["gap_relative_percent"])
            worst = max(comparable, key=lambda r: r["gap_relative_percent"])
            print(f"Mejor gap: {best['instance']} = {best['gap_relative_percent']:.2f}%")
            print(f"Peor gap: {worst['instance']} = {worst['gap_relative_percent']:.2f}%")
    print("[OUT] runs:", args.out_dir / "resumen_runs.csv")
    print("[OUT] comparación:", args.out_dir / "comparacion_oficial_competicion.csv")


if __name__ == "__main__":
    main()
