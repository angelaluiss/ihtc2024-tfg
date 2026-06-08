"""
ejecutar_ablacion_sin_readmision.py
===================================
Variante de ejecutar_rapido.py con la READMISIÓN DESACTIVADA, para el estudio
de ablación. Todo lo demás es IDÉNTICO (mismos tiempos, misma semilla, mismos
hilos), de modo que la única diferencia frente a ejecutar_rapido es la
readmisión. Así el delta de coste se atribuye limpiamente a ese componente.

Salida en carpeta propia: resultados_ablacion_benchmark/

Uso (recomendado: solo las instancias que pasan por poda):
  python ejecutar_ablacion_sin_readmision.py --instancias i13,i15,i16,i17,i20,i21,i22,i26,i27,i29,i30
  python ejecutar_ablacion_sin_readmision.py            # las 30
"""

import ejecutar_maximo as em

# Mismos parámetros que ejecutar_rapido.py ...
em.MIP_GAP   = 0.01
em.SA_T0     = 100.0
em.SA_ALPHA  = 0.9995
em.SA_TMIN   = 1e-4
em.T_F1      = 240
em.T_F2_HAB  = 150
em.T_F2_OT   = 90
em.T_SA      = 300
em.T_LS      = 60
em.T_MAX_TOTAL = em.T_F1 + em.T_F2_HAB + em.T_F2_OT + em.T_SA + em.T_LS
em.MAX_ITER_FEEDBACK = 2
em.MAX_ITER_PODA     = 8

# ... salvo el ÚNICO cambio: readmisión apagada.
em.READMISION_ACTIVA = False

# Carpetas propias para no pisar el run con readmisión.
em.RES_DIR = em.BASE_DIR / "resultados_ablacion"
em.OUT_DIR = em.BASE_DIR / "resultados_ablacion_benchmark"


if __name__ == "__main__":
    em.main()
