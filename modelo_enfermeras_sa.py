"""
modelo_enfermeras_sa.py

Fase 3 completa inicial:
    Greedy inicial -> Simulated Annealing -> búsqueda local final -> validación.

Esta versión continúa la fase 3 después de:
    - modelo_enfermeras_greedy_inicio.py
    - modelo_enfermeras_busqueda_local.py

La idea es:
    1) El greedy asegura factibilidad: UncoveredRoom = 0.
    2) Simulated Annealing permite aceptar empeoramientos temporales para escapar
       de óptimos locales.
    3) Una búsqueda local final refina la mejor solución encontrada por SA.

Requisitos:
    - test01.json
    - solucion_fase2.json
    - IHTP_Validator.exe
    - modelo_enfermeras_greedy_inicio.py
    - modelo_enfermeras_busqueda_local.py
"""

from pathlib import Path
import argparse
import importlib.util
import json
import math
import random
import time


# ============================================================
# RUTAS POR DEFECTO
# ============================================================

BASE_DIR = Path(r"C:\Users\angel\OneDrive\Escritorio\tfg")

DEFAULT_INSTANCE = BASE_DIR / "test01.json"
DEFAULT_INPUT_SOLUTION = BASE_DIR / "solucion_fase2.json"
DEFAULT_OUTPUT_SOLUTION = BASE_DIR / "solucion_fase3_sa.json"
DEFAULT_STATS = BASE_DIR / "estadisticas_fase3_sa.json"
DEFAULT_VALIDATOR = BASE_DIR / "IHTP_Validator.exe"

DEFAULT_GREEDY_SCRIPT = BASE_DIR / "modelo_enfermeras_greedy_inicio.py"
DEFAULT_LOCAL_SEARCH_SCRIPT = BASE_DIR / "modelo_enfermeras_busqueda_local.py"


# ============================================================
# IMPORTACIÓN DE MÓDULOS
# ============================================================

def importar_modulo(nombre, ruta):
    ruta = Path(ruta)

    if not ruta.exists():
        raise FileNotFoundError(f"No se encuentra el archivo requerido: {ruta}")

    spec = importlib.util.spec_from_file_location(nombre, str(ruta))
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def guardar_json(data, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


# ============================================================
# EVALUACIÓN INCREMENTAL
# ============================================================
#
# En lugar de reevaluar TODA la solución en cada iteración (coste O(tareas)
# por movimiento, que es el cuello de botella del SA en instancias grandes),
# se mantiene un estado agregado y se calcula solo el impacto LOCAL de cada
# movimiento (coste O(habitaciones afectadas)).
#
# El estado guarda:
#   - nurse_load[(n,d,t)]        carga acumulada de cada enfermera-turno
#   - ec[(entidad,n)]            nº de turnos en que la enfermera n atiende a
#                                la entidad (paciente/ocupante). Permite contar
#                                la continuidad de cuidados de forma incremental.
#   - skill_raw, load_raw, cont_raw, uncovered y el score actuales.
#
# calcular_delta() es PURO (no modifica el estado): permite probar varios
# vecinos y aplicar solo el mejor con aplicar_cambios().

from collections import defaultdict as _defaultdict


class EstadoEnfermeria:
    """Estado agregado de una asignación, para evaluación incremental."""

    def __init__(self, datos, assignment):
        self.d = datos
        self.w = datos["weights"]
        self.assignment = dict(assignment)

        self.nurse_load = _defaultdict(float)
        self.ec = _defaultdict(int)
        self.skill_raw = 0.0
        self.load_raw = 0.0
        self.cont_raw = 0
        self.uncovered = 0

        for (d, t), rooms in datos["occupied_rooms"].items():
            for room in rooms:
                k = (d, t, room)
                n = self.assignment.get(k)
                if n is None:
                    self.uncovered += 1
                    continue
                ns = datos["nurse_skill"].get(n, 0)
                for req in datos["room_required_skills_list"].get(k, []):
                    self.skill_raw += max(0, req - ns)
                self.nurse_load[(n, d, t)] += datos["room_workload"].get(k, 0.0)
                for e in datos["room_patient_entities"].get((d, room), []):
                    self.ec[(e, n)] += 1

        for k, v in self.nurse_load.items():
            self.load_raw += max(0.0, v - datos["nurse_max_load"].get(k, 0.0))

        self.cont_raw = sum(1 for v in self.ec.values() if v > 0)

    def score(self):
        return (
            100000 * self.uncovered
            + self.w["room_skill_level"] * self.skill_raw
            + self.w["excessive_nurse_workload"] * self.load_raw
            + self.w["continuity_of_care"] * self.cont_raw
        )

    def eval_dict(self):
        """Formato compatible con evaluar_assignment del módulo base."""
        return {
            "uncovered_room": int(self.uncovered),
            "room_skill_level_raw": float(self.skill_raw),
            "excessive_nurse_workload_raw": float(self.load_raw),
            "continuity_of_care_raw": int(self.cont_raw),
            "score": float(self.score()),
        }


def _exceso(load, maxl):
    return load - maxl if load > maxl else 0.0


def calcular_delta(st, cambios):
    """
    Delta del score producido por una lista de cambios [(d,t,room,nueva_enfermera)].
    NO modifica el estado. Sirve tanto para una reasignación (1 cambio) como
    para un swap (2 cambios), agregando correctamente el efecto sobre cargas y
    continuidad cuando una enfermera interviene en varios cambios.
    """
    d = st.d
    d_skill = 0.0
    net_load = {}
    net_cont = {}

    for (day, sh, room, nn) in cambios:
        k = (day, sh, room)
        no = st.assignment.get(k)
        if no == nn:
            continue

        so = d["nurse_skill"].get(no, 0) if no is not None else None
        sn = d["nurse_skill"].get(nn, 0) if nn is not None else None

        for req in d["room_required_skills_list"].get(k, []):
            nuevo_def = max(0, req - sn) if nn is not None else 0
            viejo_def = max(0, req - so) if no is not None else 0
            d_skill += nuevo_def - viejo_def

        W = d["room_workload"].get(k, 0.0)
        if no is not None:
            net_load[(no, day, sh)] = net_load.get((no, day, sh), 0.0) - W
        if nn is not None:
            net_load[(nn, day, sh)] = net_load.get((nn, day, sh), 0.0) + W

        for e in d["room_patient_entities"].get((day, room), []):
            if no is not None:
                net_cont[(e, no)] = net_cont.get((e, no), 0) - 1
            if nn is not None:
                net_cont[(e, nn)] = net_cont.get((e, nn), 0) + 1

    d_load = 0.0
    for k, nd in net_load.items():
        if nd == 0:
            continue
        cur = st.nurse_load.get(k, 0.0)
        mx = d["nurse_max_load"].get(k, 0.0)
        d_load += _exceso(cur + nd, mx) - _exceso(cur, mx)

    d_cont = 0
    for k, nc in net_cont.items():
        if nc == 0:
            continue
        old = st.ec.get(k, 0)
        new = old + nc
        d_cont += (1 if new > 0 else 0) - (1 if old > 0 else 0)

    d_score = (
        st.w["room_skill_level"] * d_skill
        + st.w["excessive_nurse_workload"] * d_load
        + st.w["continuity_of_care"] * d_cont
    )

    return {
        "score": d_score,
        "skill": d_skill,
        "load": d_load,
        "cont": d_cont,
        "net_load": net_load,
        "net_cont": net_cont,
    }


def aplicar_cambios(st, cambios, delta):
    """Aplica al estado un conjunto de cambios usando el delta ya calculado."""
    for (day, sh, room, nn) in cambios:
        st.assignment[(day, sh, room)] = nn
    for k, nd in delta["net_load"].items():
        st.nurse_load[k] = st.nurse_load.get(k, 0.0) + nd
    for k, nc in delta["net_cont"].items():
        st.ec[k] = st.ec.get(k, 0) + nc
    st.skill_raw += delta["skill"]
    st.load_raw += delta["load"]
    st.cont_raw += delta["cont"]


def generar_movimiento(datos, st, tasks, tasks_by_shift, rng, probabilidades=None):
    """
    Genera un movimiento como lista de cambios (sin copiar la solución completa).
    Tipos: reasignación, swap y movimiento orientado a continuidad.
    Devuelve (cambios, tipo) o None.
    """
    if probabilidades is None:
        probabilidades = {"reasignacion": 0.55, "swap": 0.20, "continuidad": 0.25}

    tipos = list(probabilidades.keys())
    pesos = [probabilidades[t] for t in tipos]
    elegido = rng.choices(tipos, weights=pesos, k=1)[0]

    if elegido in ("reasignacion", "continuidad") and tasks:
        d, t, r = rng.choice(tasks)
        actual = st.assignment.get((d, t, r))
        disponibles = datos["available_nurses"].get((d, t), [])

        if elegido == "continuidad":
            # Enfermeras que ya atienden a alguna entidad de la habitación.
            ya_atienden = set()
            for e in datos["room_patient_entities"].get((d, r), []):
                for n in disponibles:
                    if st.ec.get((e, n), 0) > 0:
                        ya_atienden.add(n)
            candidatos = [n for n in ya_atienden if n != actual]
        else:
            candidatos = [n for n in disponibles if n != actual]

        if not candidatos:
            return None
        return [(d, t, r, rng.choice(candidatos))], elegido

    if elegido == "swap":
        validos = [k for k, v in tasks_by_shift.items() if len(v) >= 2]
        if not validos:
            return None
        d, t = rng.choice(validos)
        a1, a2 = rng.sample(tasks_by_shift[(d, t)], 2)
        n1 = st.assignment.get(a1)
        n2 = st.assignment.get(a2)
        if n1 is None or n2 is None or n1 == n2:
            return None
        return [(a1[0], a1[1], a1[2], n2), (a2[0], a2[1], a2[2], n1)], "swap"

    return None


def busqueda_local_incremental(
    datos,
    assignment_inicial,
    evaluar_assignment=None,
    max_iter=15000,
    max_no_improve=3000,
    candidates_per_iter=50,
    time_limit=60,
    seed=123,
    aceptar_empates=False,
):
    """
    Búsqueda local de mejora (hill-climbing estocástico) con evaluación
    incremental, equivalente a la del módulo de búsqueda local pero sin
    reevaluar toda la solución en cada candidato.

    En cada iteración genera varios vecinos, calcula su delta SIN modificar el
    estado y aplica el de menor delta si mejora (o empata, si
    aceptar_empates=True). Mantiene UncoveredRoom = 0 porque los movimientos
    solo usan enfermeras disponibles.
    """
    rng = random.Random(seed)

    tasks = []
    for (d, t), rooms in datos["occupied_rooms"].items():
        for r in rooms:
            tasks.append((d, t, r))

    tasks_by_shift = {}
    for (d, t, r) in tasks:
        tasks_by_shift.setdefault((d, t), []).append((d, t, r))

    st = EstadoEnfermeria(datos, assignment_inicial)
    mejor = dict(st.assignment)
    eval_mejor = st.eval_dict()
    score_mejor = eval_mejor["score"]

    start = time.time()
    no_improve = 0
    n_accept = 0

    print("[LS] Inicio búsqueda local (evaluación incremental)")
    print("[LS] Score inicial:", score_mejor)

    for it in range(1, max_iter + 1):
        if time.time() - start >= time_limit:
            print("[LS] Parada por tiempo.")
            break
        if no_improve >= max_no_improve:
            print("[LS] Parada por iteraciones sin mejora.")
            break

        best_cambios = None
        best_delta = None

        for _ in range(candidates_per_iter):
            generated = generar_movimiento(datos, st, tasks, tasks_by_shift, rng)
            if generated is None:
                continue
            cambios, _tipo = generated
            delta_cand = calcular_delta(st, cambios)

            mejora = delta_cand["score"] < 0
            empate = aceptar_empates and delta_cand["score"] == 0

            if mejora or empate:
                if best_cambios is None or delta_cand["score"] < best_delta["score"]:
                    best_cambios = cambios
                    best_delta = delta_cand

        if best_cambios is not None:
            aplicar_cambios(st, best_cambios, best_delta)
            n_accept += 1
            no_improve = 0

            score_actual = st.score()
            if score_actual < score_mejor:
                mejor = dict(st.assignment)
                eval_mejor = st.eval_dict()
                score_mejor = score_actual

                if it % 100 == 0:
                    print(f"[LS] it={it} score={score_mejor:.3f} "
                          f"skill={st.skill_raw} load={st.load_raw} cont={st.cont_raw}")
        else:
            no_improve += 1

    elapsed = time.time() - start

    # Comprobación de consistencia con el evaluador completo.
    if evaluar_assignment is not None:
        ef = evaluar_assignment(datos, mejor)
        if abs(ef["score"] - eval_mejor["score"]) > 1e-6:
            print("[LS][WARNING] Desajuste incremental/completo:",
                  ef["score"], "vs", eval_mejor["score"])
            eval_mejor = ef

    print("[LS] Fin búsqueda local | score mejor:", eval_mejor["score"], "| aceptados:", n_accept)

    info = {
        "elapsed_seconds": elapsed,
        "iterations_requested": max_iter,
        "accepted": n_accept,
        "incremental": True,
    }

    return mejor, eval_mejor, info


# ============================================================
# SIMULATED ANNEALING
# ============================================================

def simulated_annealing(
    datos,
    assignment_inicial,
    evaluar_assignment,
    ls_module,
    max_iter=60000,
    time_limit=180,
    T0=50.0,
    alpha=0.9995,
    Tmin=1e-4,
    seed=123,
    candidates_per_iter=1,
    aceptar_solo_sin_uncovered=True,
):
    """
    Simulated Annealing para la asignación de enfermeras.

    En cada iteración:
      - genera uno o varios vecinos;
      - escoge el mejor de ellos según el evaluador interno;
      - si mejora, lo acepta;
      - si empeora, lo acepta con probabilidad exp(-delta / T);
      - guarda siempre la mejor solución encontrada.

    Se mantiene UncoveredRoom = 0 si aceptar_solo_sin_uncovered=True.
    """
    rng = random.Random(seed)

    tasks = ls_module.tareas_ocupadas(datos)
    tasks_by_shift = ls_module.tareas_por_turno(tasks)

    # Estado con evaluación incremental. El score se mantiene actualizado
    # aplicando solo el delta de cada movimiento, sin reevaluar toda la solución.
    st = EstadoEnfermeria(datos, assignment_inicial)

    mejor = dict(st.assignment)
    eval_mejor = st.eval_dict()
    score_mejor = eval_mejor["score"]

    start = time.time()
    T = float(T0)

    n_accept = 0
    n_accept_worse = 0
    n_reject = 0
    n_improve_best = 0

    historial = []

    print("[SA] Inicio Simulated Annealing (evaluación incremental)")
    print("[SA] Score inicial:", score_mejor)
    print("[SA] Métricas iniciales:", ls_module.metricas_resumidas(eval_mejor))
    print("[SA] T0:", T0, "| alpha:", alpha, "| Tmin:", Tmin)

    for it in range(1, max_iter + 1):
        elapsed = time.time() - start

        if elapsed >= time_limit:
            print("[SA] Parada por tiempo.")
            break

        if T <= Tmin:
            print("[SA] Parada por temperatura mínima.")
            break

        best_cambios = None
        best_delta = None
        best_tipo = None

        # Se pueden probar varios vecinos y quedarse con el de menor delta.
        # calcular_delta es puro, así que se evalúan sin tocar el estado.
        for _ in range(candidates_per_iter):
            generated = generar_movimiento(datos, st, tasks, tasks_by_shift, rng)

            if generated is None:
                continue

            cambios, tipo = generated
            delta_cand = calcular_delta(st, cambios)

            if best_cambios is None or delta_cand["score"] < best_delta["score"]:
                best_cambios = cambios
                best_delta = delta_cand
                best_tipo = tipo

        if best_cambios is None:
            n_reject += 1
            T *= alpha
            continue

        delta = best_delta["score"]

        aceptar = False
        aceptado_peor = False

        if delta <= 0:
            aceptar = True
        else:
            prob = math.exp(-delta / max(T, 1e-12))
            if rng.random() < prob:
                aceptar = True
                aceptado_peor = True

        if aceptar:
            aplicar_cambios(st, best_cambios, best_delta)
            n_accept += 1

            if aceptado_peor:
                n_accept_worse += 1

            score_actual = st.score()

            if score_actual < score_mejor:
                mejor = dict(st.assignment)
                eval_mejor = st.eval_dict()
                score_mejor = score_actual
                n_improve_best += 1

                print(
                    f"[SA] Nueva mejor it={it} T={T:.4f} "
                    f"score={score_mejor:.3f} "
                    f"skill={st.skill_raw} "
                    f"load={st.load_raw} "
                    f"cont={st.cont_raw} "
                    f"move={best_tipo}"
                )
        else:
            n_reject += 1

        T *= alpha

        if it % 1000 == 0:
            historial.append({
                "iter": it,
                "elapsed": elapsed,
                "temperature": T,
                "score_actual": st.score(),
                "score_mejor": score_mejor,
                "skill_mejor": eval_mejor["room_skill_level_raw"],
                "load_mejor": eval_mejor["excessive_nurse_workload_raw"],
                "continuity_mejor": eval_mejor["continuity_of_care_raw"],
                "accepted": n_accept,
                "accepted_worse": n_accept_worse,
                "rejected": n_reject,
            })

            print(
                f"[SA] it={it} T={T:.4f} "
                f"actual={st.score():.3f} mejor={score_mejor:.3f} "
                f"acc={n_accept} worse={n_accept_worse}"
            )

    elapsed_total = time.time() - start

    # Comprobación de consistencia: el evaluador completo debe coincidir con el
    # estado incremental sobre la mejor solución encontrada.
    eval_full_mejor = evaluar_assignment(datos, mejor)
    if abs(eval_full_mejor["score"] - eval_mejor["score"]) > 1e-6:
        print("[SA][WARNING] Desajuste incremental/completo:",
              eval_full_mejor["score"], "vs", eval_mejor["score"])
        eval_mejor = eval_full_mejor

    print("[SA] Fin Simulated Annealing")
    print("[SA] Tiempo:", round(elapsed_total, 2), "s")
    print("[SA] Score mejor:", eval_mejor["score"])
    print("[SA] Métricas mejor:", ls_module.metricas_resumidas(eval_mejor))
    print("[SA] Aceptados:", n_accept, "| peores aceptados:", n_accept_worse, "| rechazados:", n_reject)

    info = {
        "elapsed_seconds": elapsed_total,
        "iterations_requested": max_iter,
        "temperature_final": T,
        "accepted": n_accept,
        "accepted_worse": n_accept_worse,
        "rejected": n_reject,
        "best_improvements": n_improve_best,
        "historial": historial,
    }

    return mejor, eval_mejor, info


# ============================================================
# FUNCIÓN PRINCIPAL
# ============================================================

def resolver_enfermeras_sa(
    ruta_instancia=DEFAULT_INSTANCE,
    ruta_sol_fase2=DEFAULT_INPUT_SOLUTION,
    ruta_salida=DEFAULT_OUTPUT_SOLUTION,
    ruta_estadisticas=DEFAULT_STATS,
    ruta_validador=DEFAULT_VALIDATOR,
    ruta_greedy_script=DEFAULT_GREEDY_SCRIPT,
    ruta_local_search_script=DEFAULT_LOCAL_SEARCH_SCRIPT,
    sa_max_iter=60000,
    sa_time_limit=180,
    sa_T0=50.0,
    sa_alpha=0.9995,
    sa_Tmin=1e-4,
    sa_candidates_per_iter=1,
    ls_final_time=60,
    ls_final_max_iter=15000,
    ls_final_max_no_improve=3000,
    ls_final_candidates_per_iter=50,
    seed=123,
    validar=True,
):
    print("\n--- FASE 3: Greedy + Simulated Annealing + búsqueda local final ---")

    ruta_instancia = Path(ruta_instancia)
    ruta_sol_fase2 = Path(ruta_sol_fase2)
    ruta_salida = Path(ruta_salida)
    ruta_estadisticas = Path(ruta_estadisticas)
    ruta_validador = Path(ruta_validador)
    ruta_greedy_script = Path(ruta_greedy_script)
    ruta_local_search_script = Path(ruta_local_search_script)

    print("[INFO] Instancia:", ruta_instancia)
    print("[INFO] Solución fase 2:", ruta_sol_fase2)
    print("[INFO] Script greedy:", ruta_greedy_script)
    print("[INFO] Script búsqueda local:", ruta_local_search_script)

    base = importar_modulo("fase3_greedy_base", ruta_greedy_script)
    ls_module = importar_modulo("fase3_local_search_base", ruta_local_search_script)

    instancia = base.load_json(ruta_instancia)
    sol_fase2 = base.load_json(ruta_sol_fase2)

    print("[INFO] Construyendo datos derivados...")
    datos = base.construir_datos_enfermeria(instancia, sol_fase2)

    total_tareas = sum(len(v) for v in datos["occupied_rooms"].values())
    print("[INFO] Tareas habitación-turno ocupadas:", total_tareas)

    # 1) Greedy inicial.
    print("[INFO] Construyendo solución greedy inicial...")
    assignment_greedy, extra_greedy = base.construir_solucion_greedy(datos)
    eval_greedy = base.evaluar_assignment(datos, assignment_greedy)

    print("[INFO] Evaluación interna greedy:")
    for k, v in eval_greedy.items():
        print(f"       {k}: {v}")

    # 2) SA.
    assignment_sa, eval_sa, info_sa = simulated_annealing(
        datos=datos,
        assignment_inicial=assignment_greedy,
        evaluar_assignment=base.evaluar_assignment,
        ls_module=ls_module,
        max_iter=sa_max_iter,
        time_limit=sa_time_limit,
        T0=sa_T0,
        alpha=sa_alpha,
        Tmin=sa_Tmin,
        seed=seed,
        candidates_per_iter=sa_candidates_per_iter,
        aceptar_solo_sin_uncovered=True,
    )

    # 3) Búsqueda local final desde la mejor solución del SA.
    #    Usa evaluación incremental (misma maquinaria que el SA), mucho más
    #    rápida que reevaluar toda la solución por cada uno de los candidatos.
    print("[INFO] Refinamiento final con búsqueda local (incremental)...")
    assignment_final, eval_final, info_ls_final = busqueda_local_incremental(
        datos=datos,
        assignment_inicial=assignment_sa,
        evaluar_assignment=base.evaluar_assignment,
        max_iter=ls_final_max_iter,
        max_no_improve=ls_final_max_no_improve,
        candidates_per_iter=ls_final_candidates_per_iter,
        time_limit=ls_final_time,
        seed=seed + 999,
        aceptar_empates=False,
    )

    print("[INFO] Evaluación interna final:")
    for k, v in eval_final.items():
        print(f"       {k}: {v}")

    # 4) Exportar.
    solucion_final = base.exportar_solucion_con_enfermeras(sol_fase2, assignment_final)
    base.save_json(solucion_final, ruta_salida)

    print("[INFO] Solución SA guardada en:", ruta_salida)

    # 5) Validar.
    resultado_validador = None

    if validar:
        print("[INFO] Ejecutando validador oficial...")
        resultado_validador = base.ejecutar_validador(ruta_instancia, ruta_salida, ruta_validador)

        if resultado_validador.get("message"):
            print("[WARNING]", resultado_validador["message"])

        parsed = resultado_validador.get("parsed", {})

        if parsed:
            print("[INFO] Resultado validador:")
            print("       Total violations:", parsed.get("total_violations"))
            print("       Total cost:", parsed.get("total_cost"))

            for k, v in parsed.get("violations", {}).items():
                print(f"       viol_{k}: {v}")

            for k, v in parsed.get("costs", {}).items():
                print(f"       cost_{k}: {v['weighted_cost']}")

    # 6) Estadísticas.
    estadisticas = {
        "input_instance": str(ruta_instancia),
        "input_solution_fase2": str(ruta_sol_fase2),
        "output_solution": str(ruta_salida),
        "num_room_shift_tasks": total_tareas,
        "greedy_internal_evaluation": eval_greedy,
        "sa_internal_evaluation": eval_sa,
        "final_internal_evaluation": eval_final,
        "greedy_extra": extra_greedy,
        "sa_info": info_sa,
        "local_search_final_info": info_ls_final,
        "validator": resultado_validador,
        "parameters": {
            "sa_max_iter": sa_max_iter,
            "sa_time_limit": sa_time_limit,
            "sa_T0": sa_T0,
            "sa_alpha": sa_alpha,
            "sa_Tmin": sa_Tmin,
            "sa_candidates_per_iter": sa_candidates_per_iter,
            "ls_final_time": ls_final_time,
            "ls_final_max_iter": ls_final_max_iter,
            "ls_final_max_no_improve": ls_final_max_no_improve,
            "ls_final_candidates_per_iter": ls_final_candidates_per_iter,
            "seed": seed,
        }
    }

    base.save_json(estadisticas, ruta_estadisticas)
    print("[INFO] Estadísticas guardadas en:", ruta_estadisticas)

    return solucion_final


# ============================================================
# CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="Fase 3: Greedy + Simulated Annealing + búsqueda local final.")

    parser.add_argument("--instancia", default=str(DEFAULT_INSTANCE), help="Ruta de la instancia JSON.")
    parser.add_argument("--entrada", default=str(DEFAULT_INPUT_SOLUTION), help="Ruta de solucion_fase2.json.")
    parser.add_argument("--salida", default=str(DEFAULT_OUTPUT_SOLUTION), help="Ruta de salida solucion_fase3_sa.json.")
    parser.add_argument("--estadisticas", default=str(DEFAULT_STATS), help="Ruta del JSON de estadísticas.")
    parser.add_argument("--validador", default=str(DEFAULT_VALIDATOR), help="Ruta de IHTP_Validator.exe.")

    parser.add_argument("--greedy-script", default=str(DEFAULT_GREEDY_SCRIPT), help="Ruta de modelo_enfermeras_greedy_inicio.py.")
    parser.add_argument("--local-script", default=str(DEFAULT_LOCAL_SEARCH_SCRIPT), help="Ruta de modelo_enfermeras_busqueda_local.py.")

    parser.add_argument("--sa-max-iter", type=int, default=60000, help="Iteraciones máximas de SA.")
    parser.add_argument("--sa-time-limit", type=float, default=180, help="Tiempo máximo de SA en segundos.")
    parser.add_argument("--sa-T0", type=float, default=50.0, help="Temperatura inicial.")
    parser.add_argument("--sa-alpha", type=float, default=0.9995, help="Factor de enfriamiento.")
    parser.add_argument("--sa-Tmin", type=float, default=1e-4, help="Temperatura mínima.")
    parser.add_argument("--sa-candidates-per-iter", type=int, default=1, help="Vecinos probados por iteración de SA.")

    parser.add_argument("--ls-final-time", type=float, default=60, help="Tiempo máximo de búsqueda local final.")
    parser.add_argument("--ls-final-max-iter", type=int, default=15000, help="Iteraciones máximas búsqueda local final.")
    parser.add_argument("--ls-final-max-no-improve", type=int, default=3000, help="Iteraciones sin mejora búsqueda local final.")
    parser.add_argument("--ls-final-candidates-per-iter", type=int, default=50, help="Vecinos por iteración de búsqueda local final.")

    parser.add_argument("--seed", type=int, default=123, help="Semilla aleatoria.")
    parser.add_argument("--no-validar", action="store_true", help="No ejecutar validador oficial.")

    args = parser.parse_args()

    resolver_enfermeras_sa(
        ruta_instancia=args.instancia,
        ruta_sol_fase2=args.entrada,
        ruta_salida=args.salida,
        ruta_estadisticas=args.estadisticas,
        ruta_validador=args.validador,
        ruta_greedy_script=args.greedy_script,
        ruta_local_search_script=args.local_script,
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
        seed=args.seed,
        validar=not args.no_validar,
    )


if __name__ == "__main__":
    main()
