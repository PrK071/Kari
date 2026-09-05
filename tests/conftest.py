import os

# Isola a suíte de testes do .env do workspace. Os testes unitarios assumem
# persistencia JSON em caminhos temporarios e runtime/ambiente de desenvolvimento;
# sem estes pins, o backend.main carrega o .env (dotenv) e os testes colidem com
# o PostgreSQL/staging reais (usuario "ja existe", CORS de producao, etc.).
#
# O gate de PostgreSQL real (tests/test_postgres_integration.py) nao e afetado:
# ele monta o proprio engine a partir de KARI_TEST_POSTGRES_URL, que continua
# sendo carregado do .env quando presente.
os.environ["KARI_ENV"] = "development"
os.environ["KARI_RUNTIME"] = "desktop"
os.environ["KARI_PERSISTENCE_BACKEND"] = "json"
os.environ["KARI_ALLOWED_ORIGINS"] = ""
os.environ["KARI_BACKEND_URL"] = ""
os.environ["KARI_FRONTEND_URL"] = ""
