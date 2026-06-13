"""
test_job_lock.py — Tests del lock distribuido de jobs (job_lock.py).

Garantía a verificar: para un mismo (job_name, period_key) solo UNA
ejecución gana el lock; las demás se saltan. Y ventanas distintas o jobs
distintos no se bloquean entre sí.
"""
import pytest

import job_lock
from job_lock import JobRun, con_lock


@pytest.fixture(autouse=True)
def _sesion_de_test(db, monkeypatch):
    """job_lock abre sus propias sesiones vía SessionLocal — en tests las
    redirigimos al engine de test (que conftest ya inyectó en database.py)."""
    import database
    monkeypatch.setattr(job_lock, "SessionLocal", database.SessionLocal)
    yield


class TestAdquirirLock:

    def test_primera_instancia_gana_el_lock(self):
        assert job_lock._adquirir_lock("job_x", "1000") is True

    def test_segunda_instancia_misma_ventana_no_corre(self):
        assert job_lock._adquirir_lock("job_x", "1000") is True
        assert job_lock._adquirir_lock("job_x", "1000") is False

    def test_ventana_siguiente_vuelve_a_correr(self):
        assert job_lock._adquirir_lock("job_x", "1000") is True
        assert job_lock._adquirir_lock("job_x", "1001") is True

    def test_jobs_distintos_no_se_bloquean(self):
        assert job_lock._adquirir_lock("job_x", "1000") is True
        assert job_lock._adquirir_lock("job_y", "1000") is True


class TestConLock:

    def test_wrapper_ejecuta_solo_una_vez_por_ventana(self):
        ejecuciones = []

        def job():
            ejecuciones.append(1)

        # Mismo nombre y ventana enorme → ambas llamadas caen en el mismo
        # period_key. Simula dos instancias disparando el job a la vez.
        wrapped_a = con_lock(job, "job_unico_test", 10**9)
        wrapped_b = con_lock(job, "job_unico_test", 10**9)
        wrapped_a()
        wrapped_b()
        assert len(ejecuciones) == 1

    def test_wrapper_deja_registro_en_job_runs(self, db):
        def job():
            pass

        con_lock(job, "job_registrado", 10**9)()
        fila = db.query(JobRun).filter(JobRun.job_name == "job_registrado").first()
        assert fila is not None

    def test_llamada_directa_al_job_sin_wrapper_no_usa_lock(self, db):
        """Los tests existentes llaman a los job_* directamente — eso debe
        seguir funcionando sin tocar la tabla de locks."""
        ejecuciones = []

        def job():
            ejecuciones.append(1)

        job()
        job()
        assert len(ejecuciones) == 2
        assert db.query(JobRun).count() == 0