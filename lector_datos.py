import json

def cargar_instancia(ruta_archivo):
    """
    Lee un archivo JSON de la competición IHTP y extrae sus componentes principales.
    """
    try:
        with open(ruta_archivo, 'r') as f:
            datos = json.load(f)
            
        print(f"--- Leyendo archivo: {ruta_archivo} ---")
        
        # 1. Parámetros Generales
        dias_horizonte = datos.get('days', 0)
        pesos_objetivo = datos.get('weights', {})
        
        # 2. Infraestructura
        quirofanos = datos.get('operating_theaters', [])
        habitaciones = datos.get('rooms', [])
        
        # 3. Personal
        cirujanos = datos.get('surgeons', [])
        enfermeras = datos.get('nurses', [])
        
        # 4. Pacientes
        pacientes = datos.get('patients', [])
        ocupantes = datos.get('occupants', []) # Pacientes que ya estaban en el hospital
        
        # Resumen por consola para verificar que todo se leyó bien
        print(f"Días de planificación: {dias_horizonte}")
        print(f"Pacientes nuevos a planificar: {len(pacientes)}")
        print(f"Pacientes ya ingresados (ocupantes): {len(ocupantes)}")
        print(f"Quirófanos disponibles: {len(quirofanos)}")
        print(f"Habitaciones disponibles: {len(habitaciones)}")
        print(f"Cirujanos: {len(cirujanos)} | Enfermeras: {len(enfermeras)}")
        print("-" * 40)
        
        # Devolvemos un diccionario estructurado con todo
        return {
            'dias': dias_horizonte,
            'pesos': pesos_objetivo,
            'quirofanos': quirofanos,
            'habitaciones': habitaciones,
            'cirujanos': cirujanos,
            'enfermeras': enfermeras,
            'pacientes': pacientes,
            'ocupantes': ocupantes
        }
        
    except FileNotFoundError:
        print(f"Error: No se encontró el archivo {ruta_archivo}.")
        return None
    except json.JSONDecodeError:
        print("Error: El archivo no tiene un formato JSON válido.")
        return None





def generar_solucion_basica(instancia, ruta_salida="solucion.json"):
    """
    Genera una solución heurística básica y la exporta a JSON.
    """
    print("\n--- Generando solución heurística básica ---")
    
    solucion_pacientes = []
    
    # Obtenemos listas útiles
    id_quirofano_por_defecto = instancia['quirofanos'][0]['id']
    todas_habitaciones = [hab['id'] for hab in instancia['habitaciones']]
    
    for paciente in instancia['pacientes']:
        id_paciente = paciente['id']
        
        # 1. Asignar día: El primer día que le permiten entrar
        dia_admision = paciente.get('surgery_release_day', 0)
        
        # 2. Asignar Quirófano: El primero de la lista (no miramos si se llena)
        quirofano_asignado = id_quirofano_por_defecto
        
        # 3. Asignar Habitación: La primera que no sea incompatible
        incompatibles = paciente.get('incompatible_room_ids', [])
        habitacion_asignada = None
        for hab in todas_habitaciones:
            if hab not in incompatibles:
                habitacion_asignada = hab
                break
                
        # Si por algún motivo todas son incompatibles, le damos la primera
        if not habitacion_asignada:
            habitacion_asignada = todas_habitaciones[0]
            
        # Guardamos la decisión para este paciente
        solucion_pacientes.append({
            "id": id_paciente,
            "admission_day": dia_admision,
            "room": habitacion_asignada,
            "operating_theater": quirofano_asignado
        })
        
    # Construimos el diccionario final que exige la competición
    solucion_final = {
        "patients": solucion_pacientes,
        "nurses": []  # Dejamos las enfermeras vacías para esta prueba inicial
    }
    
    # Exportamos a JSON
    with open(ruta_salida, 'w') as f:
        json.dump(solucion_final, f, indent=4)
        
    print(f"¡Solución exportada con éxito a '{ruta_salida}'!")
    print(f"Se han planificado {len(solucion_pacientes)} pacientes.")


# ==========================================
# BLOQUE DE PRUEBA 
# ==========================================
if __name__ == "__main__":
    nombre_archivo_datos = "test01.json"  
    
    # 1. Leemos los datos 
    instancia = cargar_instancia(nombre_archivo_datos)
    
    # 2. Generamos la solución
    if instancia:
        generar_solucion_basica(instancia, "mi_primera_solucion.json")