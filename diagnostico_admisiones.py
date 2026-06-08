import json
import itertools
import csv
import subprocess
from pathlib import Path

import gurobipy as gp
from gurobipy import GRB


# ============================================================
# CONFIGURACIÓN
# ============================================================

BASE_DIR = Path(r"C:\Users\angel\OneDrive\Escritorio\tfg")
INSTANCE_PATH = BASE_DIR / "test01.json"

PHASE2_SCRIPT = BASE_DIR / "modelo_habitaciones_gurobi.py"
VALIDATOR_EXE = BASE_DIR / "IHTP_Validator.exe"

OUT_DIR = BASE_DIR / "diagnostico_admisiones"
OUT_DIR.mkdir(parents=True, exist_ok=True)

TIME_LIMIT_ADM = 90
TIME_LIMIT_PHASE2 = 60
MIP_GAP = 0.02

RUN_PHASE2 = True
RUN_VALIDATOR = True

# Si hay muchas habitaciones/quirofanos, no conviene generar todos los subconjuntos.
# Para test01 puede dejarse None. Para instancias mayores usar 3 o 4.
MAX_HALL_ROOM_SUBSET_SIZE = None
MAX_HALL_OT_SUBSET_SIZE = None


# ============================================================
# UTILIDADES
# ============================================================

def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(data, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)


def subsets_nonempty(items, max_size=None, skip_full=True):
    n = len(items)
    max_r = n if max_size is None else min(max_size, n)

    for r in range(1, max_r + 1):
        if skip_full and r == n:
            continue
        for comb in itertools.combinations(items, r):
            yield set(comb)


def habitaciones_compatibles(paciente, room_ids):
    incompatibles = set(paciente.get("incompatible_room_ids", []))
    return [r for r in room_ids if r not in incompatibles]


def construir_ocupantes(instancia, room_ids, dias):
    ocupantes = instancia.get("occupants", [])
    generos = sorted(set(
        [p["gender"] for p in instancia["patients"]] +
        [o["gender"] for o in ocupantes]
    ))

    occ_room_day = {r: {d: [] for d in dias} for r in room_ids}
    fixed_beds = {d: 0 for d in dias}
    fixed_beds_gender = {g: {d: 0 for d in dias} for g in generos}

    for oc in ocupantes:
        rid = oc["room_id"]
        g = oc["gender"]
        for d in range(min(oc["length_of_stay"], instancia["days"])):
            if rid in occ_room_day:
                occ_room_day[rid][d].append(oc)
            fixed_beds[d] += 1
            fixed_beds_gender[g][d] += 1

    return generos, occ_room_day, fixed_beds, fixed_beds_gender


def individual_ot_fit(paciente, operating_theaters, d):
    return any(ot["availability"][d] >= paciente["surgery_duration"] for ot in operating_theaters)


def parse_validator_output(text):
    violations = {}
    costs = {}
    total_violations = None
    total_cost = None
    section = None

    import re

    for raw_line in text.splitlines():
        line = raw_line.strip()
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

        if section == "costs":
            m = re.match(r"([A-Za-z]+)\.*\s*([0-9]+)\s*\(\s*([0-9]+)\s*X\s*([0-9]+)\s*\)", line)
            if m:
                costs[m.group(1)] = {
                    "weighted_cost": int(m.group(2)),
                    "weight": int(m.group(3)),
                    "raw_cost": int(m.group(4)),
                }

    return {
        "violations": violations,
        "costs": costs,
        "total_violations": total_violations,
        "total_cost": total_cost,
    }


def run_validator(instance_path, solution_path):
    if not VALIDATOR_EXE.exists():
        return {
            "ok": False,
            "stderr": f"No existe el validador: {VALIDATOR_EXE}",
            "stdout": "",
            "total_violations": None,
            "total_cost": None,
            "violations": {},
            "costs": {},
        }

    if not Path(solution_path).exists():
        return {
            "ok": False,
            "stderr": f"No existe la solución: {solution_path}",
            "stdout": "",
            "total_violations": None,
            "total_cost": None,
            "violations": {},
            "costs": {},
        }

    cmd = [str(VALIDATOR_EXE), str(instance_path), str(solution_path)]
    res = subprocess.run(
        cmd,
        cwd=str(BASE_DIR),
        capture_output=True,
        text=True,
        timeout=120,
        shell=False,
    )

    parsed = parse_validator_output((res.stdout or "") + "\n" + (res.stderr or ""))

    return {
        "ok": res.returncode == 0,
        "stderr": res.stderr,
        "stdout": res.stdout,
        **parsed,
    }


def run_phase2(instance_path, sol_fase1, sol_fase2, feedback_path):
    """
    Ejecuta resolver_habitaciones_debug del script actual.
    Requiere que modelo_habitaciones_gurobi.py sea la versión nueva
    que asigna habitaciones y luego quirófanos.
    """
    if not PHASE2_SCRIPT.exists():
        return False, f"No existe {PHASE2_SCRIPT}"

    import importlib.util
    spec = importlib.util.spec_from_file_location("modelo_habitaciones_gurobi_diag", str(PHASE2_SCRIPT))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    try:
        mod.resolver_habitaciones_debug(
            ruta_instancia=str(instance_path),
            ruta_sol_previa=str(sol_fase1),
            ruta_final=str(sol_fase2),
            ruta_feedback=str(feedback_path),
            time_limit=TIME_LIMIT_PHASE2,
        )
        return Path(sol_fase2).exists(), ""
    except Exception as e:
        return False, repr(e)


# ============================================================
# MODELO DE DIAGNÓSTICO DE ADMISIONES
# ============================================================

def resolver_admisiones_diagnostico(instancia, config, out_dir):
    dias = list(range(instancia["days"]))
    patients = instancia["patients"]
    rooms = instancia["rooms"]
    ots = instancia["operating_theaters"]
    surgeons = instancia["surgeons"]
    weights = instancia["weights"]

    room_ids = [r["id"] for r in rooms]
    room_cap = {r["id"]: r["capacity"] for r in rooms}
    total_beds = sum(room_cap.values())

    ot_ids = [ot["id"] for ot in ots]
    ot_cap = {(ot["id"], d): ot["availability"][d] for ot in ots for d in dias}

    surgeon_ids = [s["id"] for s in surgeons]

    generos, occ_room_day, fixed_beds, fixed_beds_gender = construir_ocupantes(instancia, room_ids, dias)

    model = gp.Model(config["name"])
    model.setParam("OutputFlag", 0)
    model.setParam("TimeLimit", config.get("time_limit", TIME_LIMIT_ADM))
    model.setParam("MIPGap", config.get("mip_gap", MIP_GAP))

    a = {}

    # Variables de admisión.
    for p in patients:
        pid = p["id"]
        release = p["surgery_release_day"]
        due = p.get("surgery_due_day", instancia["days"] - 1)

        compatible_rooms = habitaciones_compatibles(p, room_ids)

        for d in dias:
            if not (release <= d <= due):
                continue

            if config.get("use_individual_ot_fit", True):
                if not individual_ot_fit(p, ots, d):
                    continue

            if config.get("use_room_compatibility_precheck", True):
                if len(compatible_rooms) == 0:
                    continue

            a[pid, d] = model.addVar(vtype=GRB.BINARY, name=f"a_{pid}_{d}")

    optional_ids = [p["id"] for p in patients if not p.get("mandatory", False)]
    u = model.addVars(optional_ids, vtype=GRB.BINARY, name="u_optional")

    open_proxy = None
    if config.get("use_open_proxy", True) or config.get("use_total_ot_capacity", True):
        open_proxy = model.addVars(ot_ids, dias, vtype=GRB.BINARY, name="open_proxy")

    theta = None
    if config.get("use_gender_beds", False):
        theta = model.addVars(room_ids, dias, generos, vtype=GRB.BINARY, name="theta_gender")

    model.update()

    # Admisión obligatoria/opcional.
    for p in patients:
        pid = p["id"]
        expr = gp.quicksum(a[pid, d] for d in dias if (pid, d) in a)

        if p.get("mandatory", False):
            model.addConstr(expr == 1, name=f"mandatory_{pid}")
        else:
            model.addConstr(expr + u[pid] == 1, name=f"optional_{pid}")

    # Capacidad total quirúrgica diaria.
    if config.get("use_total_ot_capacity", True):
        for d in dias:
            total_minutes = gp.quicksum(
                p["surgery_duration"] * a[p["id"], d]
                for p in patients
                if (p["id"], d) in a
            )

            if open_proxy is not None:
                active_cap = gp.quicksum(ot_cap[ot, d] * open_proxy[ot, d] for ot in ot_ids)
                model.addConstr(total_minutes <= active_cap, name=f"total_ot_cap_active_{d}")

                for ot in ot_ids:
                    if ot_cap[ot, d] <= 0:
                        model.addConstr(open_proxy[ot, d] == 0, name=f"closed_ot_{ot}_{d}")
            else:
                model.addConstr(total_minutes <= sum(ot_cap[ot, d] for ot in ot_ids), name=f"total_ot_cap_{d}")

    # Capacidad por cirujano.
    if config.get("use_surgeon_capacity", True):
        for s in surgeons:
            sid = s["id"]
            for d in dias:
                model.addConstr(
                    gp.quicksum(
                        p["surgery_duration"] * a[p["id"], d]
                        for p in patients
                        if p["surgeon_id"] == sid and (p["id"], d) in a
                    ) <= s["max_surgery_time"][d],
                    name=f"surgeon_cap_{sid}_{d}"
                )

    # Hall quirófanos.
    if config.get("use_hall_ot", False):
        for d in dias:
            for subset in subsets_nonempty(ot_ids, max_size=MAX_HALL_OT_SUBSET_SIZE, skip_full=True):
                outside = set(ot_ids) - subset
                cap_subset = sum(ot_cap[ot, d] for ot in subset)

                forced = gp.quicksum(
                    p["surgery_duration"] * a[p["id"], d]
                    for p in patients
                    if (p["id"], d) in a
                    and all(ot_cap[ot, d] < p["surgery_duration"] for ot in outside)
                )

                model.addConstr(forced <= cap_subset, name=f"hall_ot_{'_'.join(sorted(subset))}_{d}")

    # Capacidad total de camas.
    if config.get("use_total_beds", True):
        for d in dias:
            dyn = gp.quicksum(
                a[p["id"], d0]
                for p in patients
                for d0 in dias
                if (p["id"], d0) in a
                and d0 <= d < min(d0 + p["length_of_stay"], instancia["days"])
            )

            model.addConstr(dyn + fixed_beds[d] <= total_beds, name=f"total_beds_{d}")

    # Género mediante partición de habitaciones.
    if config.get("use_gender_beds", False):
        for rid in room_ids:
            for d in dias:
                model.addConstr(
                    gp.quicksum(theta[rid, d, g] for g in generos) <= 1,
                    name=f"one_gender_{rid}_{d}"
                )

                fixed_genders = sorted(set(oc["gender"] for oc in occ_room_day[rid][d]))
                for g in fixed_genders:
                    model.addConstr(theta[rid, d, g] == 1, name=f"fixed_gender_{rid}_{d}_{g}")

        for g in generos:
            for d in dias:
                dyn_g = gp.quicksum(
                    a[p["id"], d0]
                    for p in patients
                    for d0 in dias
                    if (p["id"], d0) in a
                    and p["gender"] == g
                    and d0 <= d < min(d0 + p["length_of_stay"], instancia["days"])
                )

                cap_g = gp.quicksum(room_cap[rid] * theta[rid, d, g] for rid in room_ids)

                model.addConstr(dyn_g + fixed_beds_gender[g][d] <= cap_g, name=f"gender_beds_{g}_{d}")

    # Hall habitaciones.
    if config.get("use_hall_rooms", False):
        compatible_set = {p["id"]: set(habitaciones_compatibles(p, room_ids)) for p in patients}

        for d in dias:
            for subset in subsets_nonempty(room_ids, max_size=MAX_HALL_ROOM_SUBSET_SIZE, skip_full=True):
                cap_subset = sum(room_cap[r] for r in subset)
                fixed_subset = sum(1 for r in subset for oc in occ_room_day[r][d])

                forced = gp.quicksum(
                    a[p["id"], d0]
                    for p in patients
                    for d0 in dias
                    if (p["id"], d0) in a
                    and compatible_set[p["id"]].issubset(subset)
                    and d0 <= d < min(d0 + p["length_of_stay"], instancia["days"])
                )

                model.addConstr(forced + fixed_subset <= cap_subset, name=f"hall_rooms_{'_'.join(sorted(subset))}_{d}")

    # Objetivo lexicográfico.
    unscheduled = gp.quicksum(u[pid] for pid in optional_ids)

    weighted_unscheduled = gp.quicksum(weights["unscheduled_optional"] * u[pid] for pid in optional_ids)

    delay = gp.quicksum(
        weights["patient_delay"] * (d - p["surgery_release_day"]) * a[p["id"], d]
        for p in patients
        for d in dias
        if (p["id"], d) in a
    )

    if open_proxy is not None:
        open_cost = gp.quicksum(weights["open_operating_theater"] * open_proxy[ot, d] for ot in ot_ids for d in dias)
    else:
        open_cost = 0

    model.ModelSense = GRB.MINIMIZE
    model.setObjectiveN(unscheduled, index=0, priority=4, name="min_num_unscheduled")
    model.setObjectiveN(delay, index=1, priority=3, name="min_delay")
    model.setObjectiveN(open_cost, index=2, priority=2, name="min_open_proxy")

    model.optimize()

    scenario_dir = Path(out_dir)
    scenario_dir.mkdir(parents=True, exist_ok=True)

    stats = {
        "scenario": config["name"],
        "status": int(model.Status),
        "sol_count": int(model.SolCount),
        "unscheduled_optional": None,
        "scheduled_patients": None,
        "raw_delay": None,
        "open_proxy": None,
        "solution_path": str(scenario_dir / "solucion_fase1.json"),
        "phase2_ok": None,
        "validator_total_violations": None,
        "validator_total_cost": None,
        "validator_ElectiveUnscheduledPatients": None,
        "validator_PatientDelay": None,
        "validator_OpenOperatingTheater": None,
        "validator_RoomAgeMix": None,
        "validator_UncoveredRoom": None,
        "notes": "",
    }

    if model.SolCount == 0:
        stats["notes"] = "Sin solución factible en admisiones."
        save_json(stats, scenario_dir / "stats.json")
        return stats

    scheduled = []
    unsched_ids = []

    for p in patients:
        pid = p["id"]
        chosen_day = None
        for d in dias:
            if (pid, d) in a and a[pid, d].X > 0.5:
                chosen_day = d
                break

        if chosen_day is not None:
            scheduled.append({
                "id": pid,
                "admission_day": chosen_day,
                "room": None,
                "operating_theater": None
            })
        elif not p.get("mandatory", False):
            unsched_ids.append(pid)

    raw_delay = 0
    for ps in scheduled:
        p = next(pp for pp in patients if pp["id"] == ps["id"])
        raw_delay += ps["admission_day"] - p["surgery_release_day"]

    stats["unscheduled_optional"] = len(unsched_ids)
    stats["scheduled_patients"] = len(scheduled)
    stats["raw_delay"] = int(raw_delay)
    stats["open_proxy"] = int(round(sum(open_proxy[ot, d].X for ot in ot_ids for d in dias))) if open_proxy is not None else None

    solution = {"patients": scheduled, "nurses": []}
    sol_fase1_path = scenario_dir / "solucion_fase1.json"
    save_json(solution, sol_fase1_path)

    unscheduled_details = []
    for pid in unsched_ids:
        p = next(pp for pp in patients if pp["id"] == pid)
        unscheduled_details.append({
            "id": pid,
            "release": p["surgery_release_day"],
            "due": p.get("surgery_due_day"),
            "duration": p["surgery_duration"],
            "surgeon": p["surgeon_id"],
            "los": p["length_of_stay"],
            "gender": p["gender"],
            "age_group": p["age_group"],
            "num_incompatible_rooms": len(p.get("incompatible_room_ids", [])),
        })
    save_json(unscheduled_details, scenario_dir / "unscheduled_optionals.json")

    # Ejecutar fase habitaciones + quirófanos si se desea.
    if RUN_PHASE2:
        sol_fase2_path = scenario_dir / "solucion_fase2.json"
        feedback_path = scenario_dir / "feedback.json"

        ok_phase2, error = run_phase2(INSTANCE_PATH, sol_fase1_path, sol_fase2_path, feedback_path)
        stats["phase2_ok"] = bool(ok_phase2)

        if error:
            stats["notes"] = f"Error fase2: {error}"

        if RUN_VALIDATOR and ok_phase2:
            val = run_validator(INSTANCE_PATH, sol_fase2_path)
            stats["validator_total_violations"] = val["total_violations"]
            stats["validator_total_cost"] = val["total_cost"]

            for cost_name in ["ElectiveUnscheduledPatients", "PatientDelay", "OpenOperatingTheater", "RoomAgeMix"]:
                if cost_name in val["costs"]:
                    stats[f"validator_{cost_name}"] = val["costs"][cost_name]["weighted_cost"]

            if "UncoveredRoom" in val["violations"]:
                stats["validator_UncoveredRoom"] = val["violations"]["UncoveredRoom"]

            save_json({
                "ok": val["ok"],
                "stdout": val["stdout"],
                "stderr": val["stderr"],
                "violations": val["violations"],
                "costs": val["costs"],
                "total_violations": val["total_violations"],
                "total_cost": val["total_cost"],
            }, scenario_dir / "validator.json")

    save_json(stats, scenario_dir / "stats.json")
    return stats


# ============================================================
# ESCENARIOS DE DIAGNÓSTICO
# ============================================================

def build_scenarios():
    """
    Los escenarios se leen de menos a más restrictivos para ver dónde aparece
    el cuello de botella de los 8 opcionales no programados.
    """
    base = {
        "use_individual_ot_fit": True,
        "use_room_compatibility_precheck": True,
        "use_total_ot_capacity": True,
        "use_surgeon_capacity": True,
        "use_hall_ot": True,
        "use_total_beds": True,
        "use_gender_beds": True,
        "use_hall_rooms": True,
        "use_open_proxy": True,
    }

    scenarios = []

    scenarios.append({
        **base,
        "name": "00_modelo_actual_completo"
    })

    scenarios.append({
        **base,
        "name": "01_sin_hall_quirofanos",
        "use_hall_ot": False
    })

    scenarios.append({
        **base,
        "name": "02_sin_hall_habitaciones",
        "use_hall_rooms": False
    })

    scenarios.append({
        **base,
        "name": "03_sin_capacidad_genero",
        "use_gender_beds": False
    })

    scenarios.append({
        **base,
        "name": "04_sin_hall_habitaciones_ni_genero",
        "use_hall_rooms": False,
        "use_gender_beds": False
    })

    scenarios.append({
        **base,
        "name": "05_sin_anticipacion_camas",
        "use_total_beds": False,
        "use_gender_beds": False,
        "use_hall_rooms": False
    })

    scenarios.append({
        **base,
        "name": "06_solo_quirofanos_y_cirujanos",
        "use_total_beds": False,
        "use_gender_beds": False,
        "use_hall_rooms": False,
        "use_hall_ot": False
    })

    scenarios.append({
        **base,
        "name": "07_sin_capacidad_cirujanos",
        "use_surgeon_capacity": False
    })

    scenarios.append({
        **base,
        "name": "08_sin_capacidad_total_quirofanos",
        "use_total_ot_capacity": False,
        "use_hall_ot": False,
        "use_open_proxy": False
    })

    scenarios.append({
        **base,
        "name": "09_solo_ventanas",
        "use_total_ot_capacity": False,
        "use_surgeon_capacity": False,
        "use_hall_ot": False,
        "use_total_beds": False,
        "use_gender_beds": False,
        "use_hall_rooms": False,
        "use_open_proxy": False
    })

    return scenarios


# ============================================================
# MAIN
# ============================================================

def main():
    instancia = load_json(INSTANCE_PATH)

    print("=" * 80)
    print("DIAGNÓSTICO DE ADMISIONES")
    print("=" * 80)
    print("Instancia:", INSTANCE_PATH)
    print("Salida:", OUT_DIR)
    print("Fase2:", PHASE2_SCRIPT, "| existe:", PHASE2_SCRIPT.exists())
    print("Validador:", VALIDATOR_EXE, "| existe:", VALIDATOR_EXE.exists())
    print()

    scenarios = build_scenarios()
    rows = []

    for cfg in scenarios:
        print("\n" + "-" * 80)
        print("Escenario:", cfg["name"])
        print("-" * 80)

        scenario_out = OUT_DIR / cfg["name"]
        stats = resolver_admisiones_diagnostico(instancia, cfg, scenario_out)
        rows.append(stats)

        print("Resumen:")
        for key in [
            "unscheduled_optional",
            "scheduled_patients",
            "raw_delay",
            "open_proxy",
            "phase2_ok",
            "validator_total_violations",
            "validator_total_cost",
            "validator_ElectiveUnscheduledPatients",
            "validator_PatientDelay",
            "validator_OpenOperatingTheater",
            "validator_RoomAgeMix",
            "validator_UncoveredRoom",
            "notes",
        ]:
            print(f"  {key}: {stats.get(key)}")

    # CSV resumen
    csv_path = OUT_DIR / "resumen_diagnostico.csv"
    columns = [
        "scenario",
        "status",
        "sol_count",
        "unscheduled_optional",
        "scheduled_patients",
        "raw_delay",
        "open_proxy",
        "phase2_ok",
        "validator_total_violations",
        "validator_total_cost",
        "validator_ElectiveUnscheduledPatients",
        "validator_PatientDelay",
        "validator_OpenOperatingTheater",
        "validator_RoomAgeMix",
        "validator_UncoveredRoom",
        "notes",
        "solution_path",
    ]

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({c: row.get(c) for c in columns})

    print("\n" + "=" * 80)
    print("Diagnóstico terminado")
    print("CSV:", csv_path)
    print("=" * 80)


if __name__ == "__main__":
    main()
