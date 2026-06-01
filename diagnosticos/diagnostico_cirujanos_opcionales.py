import json
import csv
import itertools
import subprocess
import importlib.util
from pathlib import Path

import gurobipy as gp
from gurobipy import GRB


# ============================================================
# CONFIGURACIÓN
# ============================================================

BASE_DIR = Path(r"C:\Users\angel\OneDrive\Escritorio\tfg")
INSTANCE_PATH = BASE_DIR / "test01.json"

# Solución base que se analizará.
# Si no existe, el script intentará buscar otra solución reciente.
BASE_SOLUTION_CANDIDATES = [
    BASE_DIR / "diagnostico_admisiones" / "00_modelo_actual_completo" / "solucion_fase1.json",
    BASE_DIR / "resultados_dashboard_notebook" / "iter_01" / "solucion_fase2.json",
    BASE_DIR / "resultados_dashboard_notebook" / "iter_00" / "solucion_fase2.json",
    BASE_DIR / "solucion_fase2.json",
    BASE_DIR / "solucion_fase1.json",
]

PHASE2_SCRIPT = BASE_DIR / "modelo_habitaciones_gurobi.py"
VALIDATOR_EXE = BASE_DIR / "IHTP_Validator.exe"

OUT_DIR = BASE_DIR / "diagnostico_cirujanos_opcionales"
OUT_DIR.mkdir(parents=True, exist_ok=True)

TIME_LIMIT_FORCE = 90
MIP_GAP = 0.02

# Experimentos de forzado:
# Fuerza uno a uno cada opcional no programado para ver si el modelo puede incluirlo.
RUN_FORCED_EXPERIMENTS = True

# Completa cada solución forzada con habitaciones + quirófanos.
# Puede tardar más, pero es el test más útil.
RUN_PHASE2_FORCED = True

# Valida cada solución forzada completa.
RUN_VALIDATOR_FORCED = True

# Para instancias pequeñas se pueden usar todos los subconjuntos.
# Para instancias grandes conviene poner 3 o 4.
MAX_HALL_ROOM_SUBSET_SIZE = None
MAX_HALL_OT_SUBSET_SIZE = None


# ============================================================
# UTILIDADES
# ============================================================

def load_json(path):
    path = Path(path)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(data, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)


def write_csv(rows, path, columns=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if columns is None:
        keys = set()
        for r in rows:
            keys.update(r.keys())
        columns = list(keys)

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({c: row.get(c) for c in columns})


def find_base_solution():
    for p in BASE_SOLUTION_CANDIDATES:
        if p.exists():
            return p
    raise FileNotFoundError("No se encontró ninguna solución base. Revisa BASE_SOLUTION_CANDIDATES.")


def subsets_nonempty(items, max_size=None, skip_full=True):
    n = len(items)
    max_r = n if max_size is None else min(max_size, n)

    for r in range(1, max_r + 1):
        if skip_full and r == n:
            continue
        for comb in itertools.combinations(items, r):
            yield set(comb)


def habitaciones_compatibles(p, room_ids):
    inc = set(p.get("incompatible_room_ids", []))
    return [r for r in room_ids if r not in inc]


def individual_ot_fit(p, ots, d):
    dur = p["surgery_duration"]
    return any(ot["availability"][d] >= dur for ot in ots)


def construir_ocupantes(instancia, room_ids, dias):
    occupants = instancia.get("occupants", [])

    genders = sorted(set(
        [p["gender"] for p in instancia["patients"]] +
        [o["gender"] for o in occupants]
    ))

    occ_room_day = {r: {d: [] for d in dias} for r in room_ids}
    fixed_beds = {d: 0 for d in dias}
    fixed_beds_gender = {g: {d: 0 for d in dias} for g in genders}

    for oc in occupants:
        rid = oc["room_id"]
        g = oc["gender"]
        for d in range(min(oc["length_of_stay"], instancia["days"])):
            if rid in occ_room_day:
                occ_room_day[rid][d].append(oc)
            fixed_beds[d] += 1
            fixed_beds_gender[g][d] += 1

    return genders, occ_room_day, fixed_beds, fixed_beds_gender


def parse_validator_output(text):
    import re

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
            "stdout": "",
            "stderr": f"No existe el validador: {VALIDATOR_EXE}",
            "total_violations": None,
            "total_cost": None,
            "violations": {},
            "costs": {},
        }

    if not Path(solution_path).exists():
        return {
            "ok": False,
            "stdout": "",
            "stderr": f"No existe la solución: {solution_path}",
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
        "stdout": res.stdout,
        "stderr": res.stderr,
        **parsed,
    }


def run_phase2(sol_fase1, sol_fase2, feedback_path):
    if not PHASE2_SCRIPT.exists():
        return False, f"No existe {PHASE2_SCRIPT}"

    spec = importlib.util.spec_from_file_location("modelo_habitaciones_gurobi_force", str(PHASE2_SCRIPT))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    try:
        mod.resolver_habitaciones_debug(
            ruta_instancia=str(INSTANCE_PATH),
            ruta_sol_previa=str(sol_fase1),
            ruta_final=str(sol_fase2),
            ruta_feedback=str(feedback_path),
            time_limit=60,
        )
        return Path(sol_fase2).exists(), ""
    except Exception as e:
        return False, repr(e)


# ============================================================
# ANÁLISIS PASIVO DE CUELLOS DE BOTELLA
# ============================================================

def analizar_solucion_base(instancia, sol):
    dias = list(range(instancia["days"]))
    patients = instancia["patients"]
    patient_by_id = {p["id"]: p for p in patients}
    surgeons = instancia["surgeons"]
    ots = instancia["operating_theaters"]
    rooms = instancia["rooms"]

    room_ids = [r["id"] for r in rooms]
    ot_ids = [ot["id"] for ot in ots]

    scheduled_ids = set(ps["id"] for ps in sol.get("patients", []))
    optional_ids = set(p["id"] for p in patients if not p.get("mandatory", False))
    unscheduled_optional_ids = sorted(optional_ids - scheduled_ids)

    # Carga quirúrgica por cirujano y día en la solución base.
    scheduled_minutes_surgeon_day = {
        s["id"]: {d: 0 for d in dias}
        for s in surgeons
    }

    scheduled_minutes_day = {d: 0 for d in dias}

    for ps in sol.get("patients", []):
        pid = ps["id"]
        p = patient_by_id[pid]
        d = ps["admission_day"]
        sid = p["surgeon_id"]
        dur = p["surgery_duration"]
        scheduled_minutes_surgeon_day[sid][d] += dur
        scheduled_minutes_day[d] += dur

    surgeon_rows = []
    surgeon_by_id = {s["id"]: s for s in surgeons}

    for s in surgeons:
        sid = s["id"]
        unscheduled_of_s = [pid for pid in unscheduled_optional_ids if patient_by_id[pid]["surgeon_id"] == sid]
        scheduled_of_s = [
            pid for pid in scheduled_ids
            if patient_by_id[pid]["surgeon_id"] == sid
        ]

        total_capacity = sum(s["max_surgery_time"][d] for d in dias)
        used = sum(scheduled_minutes_surgeon_day[sid][d] for d in dias)
        residual = total_capacity - used
        unsched_minutes = sum(patient_by_id[pid]["surgery_duration"] for pid in unscheduled_of_s)

        surgeon_rows.append({
            "surgeon": sid,
            "scheduled_patients": len(scheduled_of_s),
            "unscheduled_optional_patients": len(unscheduled_of_s),
            "unscheduled_optional_ids": ",".join(unscheduled_of_s),
            "total_capacity_horizon": total_capacity,
            "used_minutes_solution": used,
            "residual_minutes_solution": residual,
            "unscheduled_optional_minutes": unsched_minutes,
            "residual_minus_unscheduled_minutes": residual - unsched_minutes,
        })

    # Capacidad diaria por cirujano.
    daily_rows = []
    for s in surgeons:
        sid = s["id"]
        for d in dias:
            cap = s["max_surgery_time"][d]
            used = scheduled_minutes_surgeon_day[sid][d]
            daily_rows.append({
                "surgeon": sid,
                "day": d,
                "capacity": cap,
                "used": used,
                "residual": cap - used,
            })

    # Análisis por paciente opcional no programado.
    patient_rows = []

    total_ot_capacity = {
        d: sum(ot["availability"][d] for ot in ots)
        for d in dias
    }

    for pid in unscheduled_optional_ids:
        p = patient_by_id[pid]
        sid = p["surgeon_id"]
        dur = p["surgery_duration"]
        release = p["surgery_release_day"]
        due = p.get("surgery_due_day", instancia["days"] - 1)

        window_days = [d for d in dias if release <= d <= due]
        days_individual_ot = [d for d in window_days if individual_ot_fit(p, ots, d)]

        days_surgeon_residual = [
            d for d in days_individual_ot
            if surgeon_by_id[sid]["max_surgery_time"][d] - scheduled_minutes_surgeon_day[sid][d] >= dur
        ]

        days_total_ot_residual = [
            d for d in days_individual_ot
            if total_ot_capacity[d] - scheduled_minutes_day[d] >= dur
        ]

        days_both_residual = [
            d for d in days_individual_ot
            if (
                surgeon_by_id[sid]["max_surgery_time"][d] - scheduled_minutes_surgeon_day[sid][d] >= dur
                and total_ot_capacity[d] - scheduled_minutes_day[d] >= dur
            )
        ]

        compatible_rooms = habitaciones_compatibles(p, room_ids)

        if not window_days:
            reason = "sin_dias_en_ventana"
        elif not days_individual_ot:
            reason = "no_cabe_individualmente_en_quirofano_en_su_ventana"
        elif not days_surgeon_residual:
            reason = "sin_hueco_residual_de_cirujano_en_solucion_base"
        elif not days_total_ot_residual:
            reason = "sin_hueco_residual_total_de_quirofano_en_solucion_base"
        elif not days_both_residual:
            reason = "huecos_separados_cirujano_y_quirofano_no_coinciden"
        elif len(compatible_rooms) == 0:
            reason = "sin_habitaciones_compatibles"
        else:
            reason = "hay_hueco_individual_en_solucion_base_requiere_reoptimizacion"

        patient_rows.append({
            "patient": pid,
            "surgeon": sid,
            "release": release,
            "due": due,
            "window_days": ",".join(map(str, window_days)),
            "duration": dur,
            "length_of_stay": p["length_of_stay"],
            "gender": p["gender"],
            "age_group": p["age_group"],
            "compatible_rooms": len(compatible_rooms),
            "incompatible_rooms": len(p.get("incompatible_room_ids", [])),
            "days_individual_ot": ",".join(map(str, days_individual_ot)),
            "days_surgeon_residual": ",".join(map(str, days_surgeon_residual)),
            "days_total_ot_residual": ",".join(map(str, days_total_ot_residual)),
            "days_both_residual": ",".join(map(str, days_both_residual)),
            "diagnostic_reason": reason,
        })

    return {
        "scheduled_ids": scheduled_ids,
        "unscheduled_optional_ids": unscheduled_optional_ids,
        "surgeon_rows": surgeon_rows,
        "daily_rows": daily_rows,
        "patient_rows": patient_rows,
    }


# ============================================================
# MODELO FORZANDO UN PACIENTE OPCIONAL
# ============================================================

def resolver_admisiones_forzando(instancia, force_patient_id, out_dir):
    """
    Reoptimiza admisiones con las restricciones completas actuales,
    forzando a que un paciente opcional concreto sea programado.

    Sirve para saber si dicho paciente puede entrar a costa de mover fechas
    o dejar fuera a otros opcionales.
    """
    dias = list(range(instancia["days"]))
    patients = instancia["patients"]
    rooms = instancia["rooms"]
    ots = instancia["operating_theaters"]
    surgeons = instancia["surgeons"]
    weights = instancia["weights"]

    patient_by_id = {p["id"]: p for p in patients}

    room_ids = [r["id"] for r in rooms]
    room_cap = {r["id"]: r["capacity"] for r in rooms}

    ot_ids = [ot["id"] for ot in ots]
    ot_cap = {(ot["id"], d): ot["availability"][d] for ot in ots for d in dias}

    genders, occ_room_day, fixed_beds, fixed_beds_gender = construir_ocupantes(instancia, room_ids, dias)

    total_beds = sum(room_cap.values())

    m = gp.Model(f"force_{force_patient_id}")
    m.setParam("OutputFlag", 0)
    m.setParam("TimeLimit", TIME_LIMIT_FORCE)
    m.setParam("MIPGap", 0.02)

    a = {}

    for p in patients:
        pid = p["id"]
        release = p["surgery_release_day"]
        due = p.get("surgery_due_day", instancia["days"] - 1)

        comp_rooms = habitaciones_compatibles(p, room_ids)

        for d in dias:
            if release <= d <= due and len(comp_rooms) > 0 and individual_ot_fit(p, ots, d):
                a[pid, d] = m.addVar(vtype=GRB.BINARY, name=f"a_{pid}_{d}")

    optional_ids = [p["id"] for p in patients if not p.get("mandatory", False)]
    u = m.addVars(optional_ids, vtype=GRB.BINARY, name="u_optional")

    open_proxy = m.addVars(ot_ids, dias, vtype=GRB.BINARY, name="open_proxy")
    theta = m.addVars(room_ids, dias, genders, vtype=GRB.BINARY, name="theta_gender")

    m.update()

    # Admisión.
    for p in patients:
        pid = p["id"]
        expr = gp.quicksum(a[pid, d] for d in dias if (pid, d) in a)

        if p.get("mandatory", False):
            m.addConstr(expr == 1, name=f"mandatory_{pid}")
        else:
            m.addConstr(expr + u[pid] == 1, name=f"optional_{pid}")

    # Forzar el paciente.
    m.addConstr(
        gp.quicksum(a[force_patient_id, d] for d in dias if (force_patient_id, d) in a) == 1,
        name=f"force_{force_patient_id}"
    )

    # Capacidad total quirófanos activados.
    for d in dias:
        total_minutes = gp.quicksum(
            p["surgery_duration"] * a[p["id"], d]
            for p in patients
            if (p["id"], d) in a
        )

        active_capacity = gp.quicksum(ot_cap[ot, d] * open_proxy[ot, d] for ot in ot_ids)

        m.addConstr(total_minutes <= active_capacity, name=f"total_ot_active_{d}")

        for ot in ot_ids:
            if ot_cap[ot, d] <= 0:
                m.addConstr(open_proxy[ot, d] == 0, name=f"closed_ot_{ot}_{d}")

    # Capacidad por cirujano.
    for s in surgeons:
        sid = s["id"]
        for d in dias:
            m.addConstr(
                gp.quicksum(
                    p["surgery_duration"] * a[p["id"], d]
                    for p in patients
                    if p["surgeon_id"] == sid and (p["id"], d) in a
                ) <= s["max_surgery_time"][d],
                name=f"surgeon_cap_{sid}_{d}"
            )

    # Hall quirófanos.
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

            m.addConstr(forced <= cap_subset, name=f"hall_ot_{'_'.join(sorted(subset))}_{d}")

    # Capacidad total camas.
    for d in dias:
        dyn = gp.quicksum(
            a[p["id"], d0]
            for p in patients
            for d0 in dias
            if (p["id"], d0) in a
            and d0 <= d < min(d0 + p["length_of_stay"], instancia["days"])
        )

        m.addConstr(dyn + fixed_beds[d] <= total_beds, name=f"total_beds_{d}")

    # Capacidad por género.
    for rid in room_ids:
        for d in dias:
            m.addConstr(
                gp.quicksum(theta[rid, d, g] for g in genders) <= 1,
                name=f"one_gender_{rid}_{d}"
            )

            fixed_genders = sorted(set(oc["gender"] for oc in occ_room_day[rid][d]))
            for g in fixed_genders:
                m.addConstr(theta[rid, d, g] == 1, name=f"fixed_gender_{rid}_{d}_{g}")

    for g in genders:
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

            m.addConstr(dyn_g + fixed_beds_gender[g][d] <= cap_g, name=f"gender_beds_{g}_{d}")

    # Hall habitaciones.
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

            m.addConstr(forced + fixed_subset <= cap_subset, name=f"hall_rooms_{'_'.join(sorted(subset))}_{d}")

    # Objetivos lexicográficos.
    unscheduled = gp.quicksum(u[pid] for pid in optional_ids)

    delay = gp.quicksum(
        weights["patient_delay"] * (d - p["surgery_release_day"]) * a[p["id"], d]
        for p in patients
        for d in dias
        if (p["id"], d) in a
    )

    open_cost = gp.quicksum(
        weights["open_operating_theater"] * open_proxy[ot, d]
        for ot in ot_ids
        for d in dias
    )

    m.ModelSense = GRB.MINIMIZE
    m.setObjectiveN(unscheduled, index=0, priority=4, name="min_unscheduled")
    m.setObjectiveN(delay, index=1, priority=3, name="min_delay")
    m.setObjectiveN(open_cost, index=2, priority=2, name="min_open_proxy")

    m.optimize()

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    result = {
        "forced_patient": force_patient_id,
        "status": int(m.Status),
        "sol_count": int(m.SolCount),
        "admission_feasible": bool(m.SolCount > 0),
        "forced_day": None,
        "unscheduled_optional": None,
        "scheduled_patients": None,
        "raw_delay": None,
        "phase2_ok": None,
        "validator_total_cost": None,
        "validator_total_violations": None,
        "validator_ElectiveUnscheduledPatients": None,
        "validator_PatientDelay": None,
        "validator_OpenOperatingTheater": None,
        "validator_SurgeonTransfer": None,
        "validator_RoomAgeMix": None,
        "validator_UncoveredRoom": None,
        "new_unscheduled_optionals": None,
        "dropped_optionals_relative_to_base": None,
        "notes": "",
    }

    if m.SolCount == 0:
        save_json(result, out_dir / "forced_result.json")
        return result, None

    solution_patients = []
    new_unscheduled = []

    for p in patients:
        pid = p["id"]
        chosen_day = None

        for d in dias:
            if (pid, d) in a and a[pid, d].X > 0.5:
                chosen_day = d
                break

        if chosen_day is not None:
            solution_patients.append({
                "id": pid,
                "admission_day": chosen_day,
                "room": None,
                "operating_theater": None
            })
            if pid == force_patient_id:
                result["forced_day"] = chosen_day
        elif not p.get("mandatory", False):
            new_unscheduled.append(pid)

    raw_delay = 0
    for ps in solution_patients:
        p = patient_by_id[ps["id"]]
        raw_delay += ps["admission_day"] - p["surgery_release_day"]

    result["unscheduled_optional"] = len(new_unscheduled)
    result["scheduled_patients"] = len(solution_patients)
    result["raw_delay"] = int(raw_delay)
    result["new_unscheduled_optionals"] = ",".join(sorted(new_unscheduled))

    sol_fase1 = out_dir / "solucion_fase1_forzada.json"
    save_json({"patients": solution_patients, "nurses": []}, sol_fase1)

    return result, sol_fase1


# ============================================================
# MAIN
# ============================================================

def main():
    instancia = load_json(INSTANCE_PATH)
    base_solution_path = find_base_solution()
    base_sol = load_json(base_solution_path)

    print("=" * 80)
    print("DIAGNÓSTICO DE OPCIONALES NO PROGRAMADOS POR CIRUJANO")
    print("=" * 80)
    print("Instancia:", INSTANCE_PATH)
    print("Solución base:", base_solution_path)
    print("Salida:", OUT_DIR)
    print()

    passive = analizar_solucion_base(instancia, base_sol)

    write_csv(
        passive["patient_rows"],
        OUT_DIR / "01_opcionales_no_programados.csv",
        columns=[
            "patient",
            "surgeon",
            "release",
            "due",
            "window_days",
            "duration",
            "length_of_stay",
            "gender",
            "age_group",
            "compatible_rooms",
            "incompatible_rooms",
            "days_individual_ot",
            "days_surgeon_residual",
            "days_total_ot_residual",
            "days_both_residual",
            "diagnostic_reason",
        ]
    )

    write_csv(
        passive["surgeon_rows"],
        OUT_DIR / "02_resumen_por_cirujano.csv",
        columns=[
            "surgeon",
            "scheduled_patients",
            "unscheduled_optional_patients",
            "unscheduled_optional_ids",
            "total_capacity_horizon",
            "used_minutes_solution",
            "residual_minutes_solution",
            "unscheduled_optional_minutes",
            "residual_minus_unscheduled_minutes",
        ]
    )

    write_csv(
        passive["daily_rows"],
        OUT_DIR / "03_capacidad_diaria_cirujanos.csv",
        columns=["surgeon", "day", "capacity", "used", "residual"]
    )

    print("[INFO] Opcionales no programados:", passive["unscheduled_optional_ids"])
    print("[INFO] Tablas pasivas guardadas.")

    forced_rows = []

    if RUN_FORCED_EXPERIMENTS:
        base_scheduled_ids = passive["scheduled_ids"]
        base_optional_unscheduled = set(passive["unscheduled_optional_ids"])

        for pid in passive["unscheduled_optional_ids"]:
            print("\n" + "-" * 80)
            print(f"Forzando paciente opcional {pid}")
            print("-" * 80)

            forced_dir = OUT_DIR / "forzados" / pid
            result, sol_fase1 = resolver_admisiones_forzando(instancia, pid, forced_dir)

            if sol_fase1 is not None and RUN_PHASE2_FORCED:
                sol_fase2 = forced_dir / "solucion_fase2_forzada.json"
                feedback = forced_dir / "feedback_forzado.json"

                ok_phase2, error = run_phase2(sol_fase1, sol_fase2, feedback)
                result["phase2_ok"] = bool(ok_phase2)

                if error:
                    result["notes"] = "Error fase2: " + error

                if ok_phase2 and RUN_VALIDATOR_FORCED:
                    val = run_validator(INSTANCE_PATH, sol_fase2)

                    result["validator_total_cost"] = val["total_cost"]
                    result["validator_total_violations"] = val["total_violations"]

                    for cname in [
                        "ElectiveUnscheduledPatients",
                        "PatientDelay",
                        "OpenOperatingTheater",
                        "SurgeonTransfer",
                        "RoomAgeMix",
                    ]:
                        if cname in val["costs"]:
                            result[f"validator_{cname}"] = val["costs"][cname]["weighted_cost"]

                    if "UncoveredRoom" in val["violations"]:
                        result["validator_UncoveredRoom"] = val["violations"]["UncoveredRoom"]

                    save_json({
                        "ok": val["ok"],
                        "stdout": val["stdout"],
                        "stderr": val["stderr"],
                        "violations": val["violations"],
                        "costs": val["costs"],
                        "total_violations": val["total_violations"],
                        "total_cost": val["total_cost"],
                    }, forced_dir / "validator.json")

            new_unscheduled_set = set(result["new_unscheduled_optionals"].split(",")) if result.get("new_unscheduled_optionals") else set()
            dropped = sorted(new_unscheduled_set - base_optional_unscheduled)
            recovered = sorted(base_optional_unscheduled - new_unscheduled_set)

            result["dropped_optionals_relative_to_base"] = ",".join(dropped)
            result["recovered_optionals_relative_to_base"] = ",".join(recovered)

            save_json(result, forced_dir / "forced_result.json")
            forced_rows.append(result)

            print("Resultado:")
            for key in [
                "admission_feasible",
                "forced_day",
                "unscheduled_optional",
                "phase2_ok",
                "validator_total_cost",
                "validator_ElectiveUnscheduledPatients",
                "validator_PatientDelay",
                "validator_OpenOperatingTheater",
                "validator_SurgeonTransfer",
                "validator_RoomAgeMix",
                "validator_UncoveredRoom",
                "new_unscheduled_optionals",
                "dropped_optionals_relative_to_base",
                "recovered_optionals_relative_to_base",
                "notes",
            ]:
                print(f"  {key}: {result.get(key)}")

        write_csv(
            forced_rows,
            OUT_DIR / "04_experimentos_forzados.csv",
            columns=[
                "forced_patient",
                "status",
                "sol_count",
                "admission_feasible",
                "forced_day",
                "unscheduled_optional",
                "scheduled_patients",
                "raw_delay",
                "phase2_ok",
                "validator_total_cost",
                "validator_total_violations",
                "validator_ElectiveUnscheduledPatients",
                "validator_PatientDelay",
                "validator_OpenOperatingTheater",
                "validator_SurgeonTransfer",
                "validator_RoomAgeMix",
                "validator_UncoveredRoom",
                "new_unscheduled_optionals",
                "dropped_optionals_relative_to_base",
                "recovered_optionals_relative_to_base",
                "notes",
            ]
        )

    # Resumen textual.
    summary_path = OUT_DIR / "00_LEEME_resumen.txt"

    with open(summary_path, "w", encoding="utf-8") as f:
        f.write("DIAGNÓSTICO DE OPCIONALES NO PROGRAMADOS\n")
        f.write("=" * 80 + "\n")
        f.write(f"Instancia: {INSTANCE_PATH}\n")
        f.write(f"Solución base: {base_solution_path}\n\n")

        f.write("Opcionales no programados en la solución base:\n")
        for row in passive["patient_rows"]:
            f.write(
                f"- {row['patient']} | cirujano={row['surgeon']} | "
                f"ventana=[{row['release']},{row['due']}] | "
                f"duración={row['duration']} | motivo={row['diagnostic_reason']}\n"
            )

        f.write("\nTablas generadas:\n")
        f.write("- 01_opcionales_no_programados.csv\n")
        f.write("- 02_resumen_por_cirujano.csv\n")
        f.write("- 03_capacidad_diaria_cirujanos.csv\n")
        if RUN_FORCED_EXPERIMENTS:
            f.write("- 04_experimentos_forzados.csv\n")

    print("\n" + "=" * 80)
    print("Diagnóstico terminado")
    print("Carpeta:", OUT_DIR)
    print("Resumen:", summary_path)
    print("=" * 80)


if __name__ == "__main__":
    main()
