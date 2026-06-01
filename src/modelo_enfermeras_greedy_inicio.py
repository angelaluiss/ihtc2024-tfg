"""
modelo_enfermeras_greedy.py

Fase 3 inicial: asignación greedy de enfermeras a habitaciones ocupadas.

Objetivo de esta primera versión:
    - leer la instancia JSON oficial,
    - leer la solución generada por fases anteriores,
    - reconstruir la ocupación de habitaciones por día y turno,
    - calcular carga y cualificación requerida por habitación-turno,
    - construir una asignación inicial greedy de enfermeras,
    - exportar una solución completa en formato JSON,
    - opcionalmente ejecutar el validador oficial si existe IHTP_Validator.exe.

Esta versión NO implementa todavía búsqueda local ni Simulated Annealing.
Su finalidad es obtener una primera solución completa y comprobar si
UncoveredRoom baja a 0.
"""

from pathlib import Path
import json
import math
import argparse
import subprocess
import re
from collections import defaultdict


# ============================================================
# CONFIGURACIÓN POR DEFECTO
# ============================================================

BASE_DIR = Path(r"C:\Users\angel\OneDrive\Escritorio\tfg")

DEFAULT_INSTANCE = BASE_DIR / "test01.json"
DEFAULT_INPUT_SOLUTION = BASE_DIR / "solucion_fase2.json"
DEFAULT_OUTPUT_SOLUTION = BASE_DIR / "solucion_fase3_greedy.json"
DEFAULT_VALIDATOR = BASE_DIR / "IHTP_Validator.exe"
DEFAULT_STATS = BASE_DIR / "estadisticas_fase3_greedy.json"


# ============================================================
# UTILIDADES BÁSICAS
# ============================================================

def load_json(path):
    path = Path(path)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(data, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


def get_shift_types(instance):
    """
    Devuelve los turnos de la instancia.
    En IHTC suelen ser early, late, night.
    """
    if "shift_types" in instance:
        return list(instance["shift_types"])

    # Fallback razonable.
    return ["early", "late", "night"]


def get_weights(instance):
    """
    Pesos oficiales, con valores por defecto si faltara algún campo.
    """
    w = instance.get("weights", {})

    return {
        "room_skill_level": w.get("room_skill_level", w.get("nurse_skill", 1)),
        "continuity_of_care": w.get("continuity_of_care", 5),
        "excessive_nurse_workload": w.get("excessive_nurse_workload", w.get("excessive_workload", 1)),
    }


def first_existing(d, keys, default=None):
    """
    Devuelve el primer valor existente de una lista de claves.
    """
    for k in keys:
        if isinstance(d, dict) and k in d:
            return d[k]
    return default


# ============================================================
# EXTRACCIÓN ROBUSTA DE CARGA Y SKILL
# ============================================================

def value_from_profile(profile, relative_day, shift, shift_index=None, default=0):
    """
    Extrae un valor de un perfil temporal con diferentes posibles formatos.

    Formatos soportados:
      1) list[relative_day][shift]
      2) list[relative_day][shift_index]
      3) dict[str(relative_day)][shift]
      4) dict[relative_day][shift]
      5) dict[shift][relative_day]
      6) valor escalar
    """
    if profile is None:
        return default

    # Perfil escalar.
    if isinstance(profile, (int, float)):
        return profile

    # Perfil como lista.
    if isinstance(profile, list):
        if relative_day < 0 or relative_day >= len(profile):
            return default

        row = profile[relative_day]

        if isinstance(row, dict):
            return row.get(shift, default)

        if isinstance(row, list):
            if shift_index is not None and 0 <= shift_index < len(row):
                return row[shift_index]
            return default

        if isinstance(row, (int, float)):
            return row

        return default

    # Perfil como diccionario.
    if isinstance(profile, dict):
        # dict[relative_day][shift]
        for key in [relative_day, str(relative_day)]:
            if key in profile:
                row = profile[key]
                if isinstance(row, dict):
                    return row.get(shift, default)
                if isinstance(row, list):
                    if shift_index is not None and 0 <= shift_index < len(row):
                        return row[shift_index]
                    return default
                if isinstance(row, (int, float)):
                    return row

        # dict[shift][relative_day]
        if shift in profile:
            row = profile[shift]
            if isinstance(row, list):
                if 0 <= relative_day < len(row):
                    return row[relative_day]
            if isinstance(row, dict):
                return row.get(relative_day, row.get(str(relative_day), default))
            if isinstance(row, (int, float)):
                return row

    return default


def get_workload(entity, relative_day, shift, shift_index=None):
    """
    Carga asistencial producida por paciente u ocupante.
    """
    profile = first_existing(
        entity,
        [
            "workload_produced",
            "workload",
            "workloads",
            "workload_production",
            "care_load",
            "load",
        ],
        None
    )
    return float(value_from_profile(profile, relative_day, shift, shift_index, default=0))


def get_required_skill(entity, relative_day, shift, shift_index=None):
    """
    Nivel mínimo de cualificación requerido por paciente u ocupante.
    """
    profile = first_existing(
        entity,
        [
            "skill_level_required",
            "required_skill",
            "skill_required",
            "care_level",
            "minimum_skill_level",
            "min_skill",
        ],
        None
    )
    return int(value_from_profile(profile, relative_day, shift, shift_index, default=0))


# ============================================================
# EXTRACCIÓN DE ENFERMERAS DISPONIBLES
# ============================================================

def get_nurse_skill(nurse):
    return int(first_existing(
        nurse,
        ["skill_level", "skill", "qualification", "level"],
        0
    ))


def get_nurse_id(nurse):
    return nurse["id"]


def parse_nurse_working_shifts(instance):
    """
    Construye:
        nurse_skill[n]
        available_nurses[(d,t)]
        nurse_max_load[(n,d,t)]

    Se intenta soportar diferentes nombres de campos habituales:
        working_shifts, shifts, availability, assignments.
    """
    days = list(range(instance["days"]))
    shift_types = get_shift_types(instance)
    nurses = instance.get("nurses", [])

    nurse_skill = {}
    available_nurses = {(d, t): [] for d in days for t in shift_types}
    nurse_max_load = {}

    for nurse in nurses:
        nid = get_nurse_id(nurse)
        nurse_skill[nid] = get_nurse_skill(nurse)

        shifts = first_existing(
            nurse,
            ["working_shifts", "shifts", "availability", "available_shifts"],
            []
        )

        # Caso especial: si viniera como diccionario por día/turno.
        if isinstance(shifts, dict):
            iterable = []
            for d_key, row in shifts.items():
                try:
                    d = int(d_key)
                except Exception:
                    continue

                if isinstance(row, dict):
                    for t, val in row.items():
                        if isinstance(val, dict):
                            max_load = first_existing(val, ["max_load", "maximum_load", "load", "capacity"], 0)
                            works = first_existing(val, ["works", "available"], True)
                        else:
                            max_load = val
                            works = True

                        if works:
                            iterable.append({"day": d, "shift": t, "max_load": max_load})

            shifts = iterable

        for sh in shifts:
            if not isinstance(sh, dict):
                continue

            d = first_existing(sh, ["day", "d"], None)
            t = first_existing(sh, ["shift", "shift_type", "type"], None)
            max_load = first_existing(
                sh,
                ["max_load", "maximum_load", "load", "capacity", "max_workload"],
                0
            )

            if d is None or t is None:
                continue

            d = int(d)
            t = str(t)

            if d not in days or t not in shift_types:
                continue

            available_nurses[(d, t)].append(nid)
            nurse_max_load[(nid, d, t)] = float(max_load)

    return nurse_skill, available_nurses, nurse_max_load


# ============================================================
# RECONSTRUCCIÓN DE OCUPACIÓN Y DATOS DERIVADOS
# ============================================================

def construir_datos_enfermeria(instance, solution):
    """
    A partir de la instancia y la solución de fase 2, reconstruye:

      room_patients[(d,r)]              -> lista de ids de pacientes/ocupantes
      occupied_rooms[(d,t)]             -> habitaciones ocupadas en día-turno
      room_workload[(d,t,r)]            -> carga total de la habitación
      room_required_skill[(d,t,r)]      -> skill máximo requerido en la habitación
      room_patient_entities[(d,r)]      -> ids para continuidad
      available_nurses[(d,t)]           -> enfermeras disponibles
      nurse_max_load[(n,d,t)]           -> carga máxima
      nurse_skill[n]                    -> cualificación

    Incluye pacientes nuevos y ocupantes iniciales.
    """
    days = list(range(instance["days"]))
    shift_types = get_shift_types(instance)
    shift_index = {t: i for i, t in enumerate(shift_types)}

    patients_by_id = {p["id"]: p for p in instance.get("patients", [])}
    rooms = instance.get("rooms", [])
    room_ids = [r["id"] for r in rooms]

    room_patients = defaultdict(list)
    room_entities = defaultdict(list)

    # Pacientes planificados en la solución fase 2.
    for ps in solution.get("patients", []):
        pid = ps["id"]
        if pid not in patients_by_id:
            continue

        room = ps.get("room")
        if room is None:
            continue

        p = patients_by_id[pid]
        admission_day = int(ps["admission_day"])
        los = int(p["length_of_stay"])

        for d in range(admission_day, min(admission_day + los, instance["days"])):
            room_patients[(d, room)].append(pid)
            room_entities[(d, room)].append(("patient", pid, d - admission_day))

    # Ocupantes iniciales.
    for occ in instance.get("occupants", []):
        oid = occ["id"]
        room = occ["room_id"]
        los = int(occ["length_of_stay"])

        for d in range(0, min(los, instance["days"])):
            room_patients[(d, room)].append(oid)
            room_entities[(d, room)].append(("occupant", oid, d))

    # Mapas auxiliares para recuperar entidades.
    occupants_by_id = {o["id"]: o for o in instance.get("occupants", [])}

    occupied_rooms = {(d, t): [] for d in days for t in shift_types}
    room_workload = {}
    room_required_skill = {}
    room_patient_entities = {}

    for d in days:
        for room in room_ids:
            entities = room_entities.get((d, room), [])

            if not entities:
                continue

            # La habitación está ocupada durante todos los turnos del día.
            for t in shift_types:
                total_workload = 0.0
                required_skill = 0
                entity_ids = []

                for kind, eid, rel_day in entities:
                    if kind == "patient":
                        ent = patients_by_id[eid]
                    else:
                        ent = occupants_by_id[eid]

                    total_workload += get_workload(ent, rel_day, t, shift_index[t])
                    required_skill = max(required_skill, get_required_skill(ent, rel_day, t, shift_index[t]))
                    entity_ids.append(eid)

                occupied_rooms[(d, t)].append(room)
                room_workload[(d, t, room)] = total_workload
                room_required_skill[(d, t, room)] = required_skill
                room_patient_entities[(d, room)] = list(entity_ids)

    nurse_skill, available_nurses, nurse_max_load = parse_nurse_working_shifts(instance)

    return {
        "days": days,
        "shift_types": shift_types,
        "room_ids": room_ids,
        "room_patients": dict(room_patients),
        "occupied_rooms": occupied_rooms,
        "room_workload": room_workload,
        "room_required_skill": room_required_skill,
        "room_patient_entities": room_patient_entities,
        "nurse_skill": nurse_skill,
        "available_nurses": available_nurses,
        "nurse_max_load": nurse_max_load,
        "weights": get_weights(instance),
    }


# ============================================================
# EVALUADOR INTERNO
# ============================================================

def evaluar_assignment(datos, assignment):
    """
    Evalúa una asignación:
        assignment[(d,t,r)] = nurse_id

    Devuelve métricas internas parecidas a las del validador:
        - uncovered_room
        - room_skill_level
        - excessive_nurse_workload
        - continuity_of_care
        - score

    Nota:
    Este evaluador es una aproximación interna para guiar el greedy.
    La evaluación definitiva debe hacerla el validador oficial.
    """
    weights = datos["weights"]

    uncovered = 0
    skill_cost_raw = 0
    workload_cost_raw = 0
    continuity_raw = 0

    nurse_load = defaultdict(float)
    entity_nurses = defaultdict(set)

    for (d, t), rooms in datos["occupied_rooms"].items():
        for room in rooms:
            key = (d, t, room)

            if key not in assignment or assignment[key] is None:
                uncovered += 1
                continue

            nurse = assignment[key]

            workload = datos["room_workload"].get(key, 0.0)
            req_skill = datos["room_required_skill"].get(key, 0)
            nurse_skill = datos["nurse_skill"].get(nurse, 0)

            skill_cost_raw += max(0, req_skill - nurse_skill)
            nurse_load[(nurse, d, t)] += workload

            for entity_id in datos["room_patient_entities"].get((d, room), []):
                entity_nurses[entity_id].add(nurse)

    for (nurse, d, t), load in nurse_load.items():
        max_load = datos["nurse_max_load"].get((nurse, d, t), 0.0)
        workload_cost_raw += max(0.0, load - max_load)

    for entity_id, nurses in entity_nurses.items():
        continuity_raw += len(nurses)

    score = (
        100000 * uncovered
        + weights["room_skill_level"] * skill_cost_raw
        + weights["excessive_nurse_workload"] * workload_cost_raw
        + weights["continuity_of_care"] * continuity_raw
    )

    return {
        "uncovered_room": int(uncovered),
        "room_skill_level_raw": float(skill_cost_raw),
        "excessive_nurse_workload_raw": float(workload_cost_raw),
        "continuity_of_care_raw": int(continuity_raw),
        "score": float(score),
    }


# ============================================================
# SOLUCIÓN INICIAL GREEDY
# ============================================================

def coste_incremental_greedy(datos, assignment, nurse_load, entity_nurses, d, t, room, nurse):
    """
    Coste incremental aproximado de asignar una habitación a una enfermera.

    Se combina:
      - déficit de cualificación,
      - exceso de carga,
      - continuidad de cuidados,
      - pequeña penalización por carga acumulada para balancear.
    """
    weights = datos["weights"]

    workload = datos["room_workload"].get((d, t, room), 0.0)
    req_skill = datos["room_required_skill"].get((d, t, room), 0)
    n_skill = datos["nurse_skill"].get(nurse, 0)

    current_load = nurse_load[(nurse, d, t)]
    max_load = datos["nurse_max_load"].get((nurse, d, t), 0.0)

    old_excess = max(0.0, current_load - max_load)
    new_excess = max(0.0, current_load + workload - max_load)
    inc_excess = new_excess - old_excess

    skill_deficit = max(0, req_skill - n_skill)

    # Penalización de continuidad:
    # si la enfermera ya atendió a la entidad antes, no penaliza;
    # si es nueva para esa entidad, aumenta el número de enfermeras distintas.
    inc_continuity = 0
    for entity_id in datos["room_patient_entities"].get((d, room), []):
        if nurse not in entity_nurses[entity_id]:
            inc_continuity += 1

    # Balance suave: preferimos no concentrar carga si hay alternativas.
    balance = 0.001 * (current_load + workload)

    return (
        weights["room_skill_level"] * skill_deficit
        + weights["excessive_nurse_workload"] * inc_excess
        + weights["continuity_of_care"] * inc_continuity
        + balance
    )


def construir_solucion_greedy(datos):
    """
    Construye una asignación inicial greedy.

    Recorre día-turno y asigna todas las habitaciones ocupadas.
    Para cada turno:
      1. ordena habitaciones por dificultad;
      2. prueba todas las enfermeras disponibles;
      3. elige la de menor coste incremental.
    """
    assignment = {}
    nurse_load = defaultdict(float)
    entity_nurses = defaultdict(set)

    uncovered_attempts = []

    for d in datos["days"]:
        for t in datos["shift_types"]:
            rooms = list(datos["occupied_rooms"].get((d, t), []))
            available = list(datos["available_nurses"].get((d, t), []))

            # Habitaciones difíciles primero:
            # más skill requerido, más carga, más pacientes.
            rooms.sort(
                key=lambda r: (
                    -datos["room_required_skill"].get((d, t, r), 0),
                    -datos["room_workload"].get((d, t, r), 0.0),
                    -len(datos["room_patient_entities"].get((d, r), [])),
                    r
                )
            )

            for room in rooms:
                if not available:
                    assignment[(d, t, room)] = None
                    uncovered_attempts.append((d, t, room, "sin_enfermeras_disponibles"))
                    continue

                best_nurse = None
                best_cost = None

                for nurse in available:
                    c = coste_incremental_greedy(
                        datos,
                        assignment,
                        nurse_load,
                        entity_nurses,
                        d, t, room, nurse
                    )

                    if best_cost is None or c < best_cost:
                        best_cost = c
                        best_nurse = nurse

                assignment[(d, t, room)] = best_nurse

                workload = datos["room_workload"].get((d, t, room), 0.0)
                nurse_load[(best_nurse, d, t)] += workload

                for entity_id in datos["room_patient_entities"].get((d, room), []):
                    entity_nurses[entity_id].add(best_nurse)

    return assignment, {
        "uncovered_attempts": uncovered_attempts,
        "nurse_load": {
            f"{n}|{d}|{t}": load
            for (n, d, t), load in nurse_load.items()
        }
    }


# ============================================================
# EXPORTACIÓN AL FORMATO DE SOLUCIÓN
# ============================================================

def exportar_solucion_con_enfermeras(solucion_base, assignment):
    """
    Convierte assignment[(d,t,r)] = nurse en el bloque nurses del JSON.

    Formato:
      "nurses": [
        {
          "id": "n00",
          "assignments": [
            {"day": 0, "shift": "early", "rooms": ["r0", "r1"]}
          ]
        }
      ]

    Las habitaciones se agrupan por enfermera, día y turno.
    """
    grouped = defaultdict(list)

    for (d, t, room), nurse in assignment.items():
        if nurse is None:
            continue
        grouped[(nurse, d, t)].append(room)

    nurse_to_assignments = defaultdict(list)

    for (nurse, d, t), rooms in sorted(grouped.items()):
        nurse_to_assignments[nurse].append({
            "day": int(d),
            "shift": t,
            "rooms": sorted(rooms)
        })

    nurses_block = []
    for nurse in sorted(nurse_to_assignments.keys()):
        nurses_block.append({
            "id": nurse,
            "assignments": nurse_to_assignments[nurse]
        })

    salida = {
        "patients": solucion_base.get("patients", []),
        "nurses": nurses_block
    }

    return salida


# ============================================================
# VALIDACIÓN OPCIONAL
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


def ejecutar_validador(ruta_instancia, ruta_solucion, ruta_validador):
    ruta_validador = Path(ruta_validador)

    if not ruta_validador.exists():
        return {
            "ok": False,
            "message": f"No existe el validador: {ruta_validador}",
            "stdout": "",
            "stderr": "",
            "parsed": {}
        }

    cmd = [str(ruta_validador), str(ruta_instancia), str(ruta_solucion)]

    try:
        res = subprocess.run(
            cmd,
            cwd=str(Path(ruta_instancia).parent),
            capture_output=True,
            text=True,
            timeout=120,
            shell=False
        )

        text = (res.stdout or "") + "\n" + (res.stderr or "")

        return {
            "ok": res.returncode == 0,
            "message": "",
            "stdout": res.stdout,
            "stderr": res.stderr,
            "parsed": parse_validator_output(text)
        }

    except Exception as e:
        return {
            "ok": False,
            "message": repr(e),
            "stdout": "",
            "stderr": "",
            "parsed": {}
        }


# ============================================================
# FUNCIÓN PRINCIPAL
# ============================================================

def resolver_enfermeras_greedy(
    ruta_instancia=DEFAULT_INSTANCE,
    ruta_sol_fase2=DEFAULT_INPUT_SOLUTION,
    ruta_salida=DEFAULT_OUTPUT_SOLUTION,
    ruta_estadisticas=DEFAULT_STATS,
    ruta_validador=DEFAULT_VALIDATOR,
    validar=True
):
    print("\n--- FASE 3 GREEDY: asignación inicial de enfermeras ---")

    ruta_instancia = Path(ruta_instancia)
    ruta_sol_fase2 = Path(ruta_sol_fase2)
    ruta_salida = Path(ruta_salida)
    ruta_estadisticas = Path(ruta_estadisticas)

    print("[INFO] Instancia:", ruta_instancia)
    print("[INFO] Solución fase 2:", ruta_sol_fase2)

    instance = load_json(ruta_instancia)
    sol_fase2 = load_json(ruta_sol_fase2)

    print("[INFO] Construyendo datos derivados de enfermería...")
    datos = construir_datos_enfermeria(instance, sol_fase2)

    n_occupied_tasks = sum(len(v) for v in datos["occupied_rooms"].values())
    n_available_entries = sum(len(v) for v in datos["available_nurses"].values())

    print(f"[INFO] Tareas habitación-turno ocupadas: {n_occupied_tasks}")
    print(f"[INFO] Entradas enfermera-turno disponibles: {n_available_entries}")
    print(f"[INFO] Enfermeras detectadas: {len(datos['nurse_skill'])}")

    assignment_vacio = {}
    eval_vacio = evaluar_assignment(datos, assignment_vacio)
    print("[INFO] Evaluación con nurses vacío:")
    print("       UncoveredRoom interno:", eval_vacio["uncovered_room"])

    print("[INFO] Construyendo solución greedy...")
    assignment, greedy_extra = construir_solucion_greedy(datos)

    eval_greedy = evaluar_assignment(datos, assignment)

    print("[INFO] Evaluación interna greedy:")
    for k, v in eval_greedy.items():
        print(f"       {k}: {v}")

    solucion_final = exportar_solucion_con_enfermeras(sol_fase2, assignment)
    save_json(solucion_final, ruta_salida)

    print("[INFO] Solución greedy guardada en:", ruta_salida)

    validator_result = None

    if validar:
        print("[INFO] Ejecutando validador oficial si está disponible...")
        validator_result = ejecutar_validador(ruta_instancia, ruta_salida, ruta_validador)

        if validator_result["message"]:
            print("[WARNING]", validator_result["message"])

        parsed = validator_result.get("parsed", {})

        if parsed:
            print("[INFO] Resultado validador:")
            print("       Total violations:", parsed.get("total_violations"))
            print("       Total cost:", parsed.get("total_cost"))

            if "violations" in parsed:
                for k, v in parsed["violations"].items():
                    print(f"       viol_{k}: {v}")

            if "costs" in parsed:
                for k, v in parsed["costs"].items():
                    print(f"       cost_{k}: {v['weighted_cost']}")

    stats = {
        "input_instance": str(ruta_instancia),
        "input_solution": str(ruta_sol_fase2),
        "output_solution": str(ruta_salida),
        "num_occupied_room_shift_tasks": n_occupied_tasks,
        "num_available_nurse_shift_entries": n_available_entries,
        "num_nurses": len(datos["nurse_skill"]),
        "internal_empty_evaluation": eval_vacio,
        "internal_greedy_evaluation": eval_greedy,
        "uncovered_attempts": greedy_extra["uncovered_attempts"],
        "validator": validator_result,
    }

    save_json(stats, ruta_estadisticas)
    print("[INFO] Estadísticas guardadas en:", ruta_estadisticas)

    return solucion_final


# ============================================================
# CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="Fase 3 greedy: asignación inicial de enfermeras.")
    parser.add_argument("--instancia", default=str(DEFAULT_INSTANCE), help="Ruta de la instancia JSON.")
    parser.add_argument("--entrada", default=str(DEFAULT_INPUT_SOLUTION), help="Ruta de solucion_fase2.json.")
    parser.add_argument("--salida", default=str(DEFAULT_OUTPUT_SOLUTION), help="Ruta de salida solucion_fase3_greedy.json.")
    parser.add_argument("--estadisticas", default=str(DEFAULT_STATS), help="Ruta de salida de estadísticas JSON.")
    parser.add_argument("--validador", default=str(DEFAULT_VALIDATOR), help="Ruta de IHTP_Validator.exe.")
    parser.add_argument("--no-validar", action="store_true", help="No ejecutar el validador oficial.")

    args = parser.parse_args()

    resolver_enfermeras_greedy(
        ruta_instancia=args.instancia,
        ruta_sol_fase2=args.entrada,
        ruta_salida=args.salida,
        ruta_estadisticas=args.estadisticas,
        ruta_validador=args.validador,
        validar=not args.no_validar
    )


if __name__ == "__main__":
    main()
