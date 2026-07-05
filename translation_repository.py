from __future__ import annotations

import os
import threading
import time
from typing import Any, Dict, List, Tuple

from app_db import get_backend, get_connection, get_connection_sqlserver_database, get_storehub_database_name

# Cache di processo per translation_map: la mappa completa (~1600 chiavi) veniva
# ricaricata da SQL Server a OGNI render di pagina (~600ms). Le traduzioni
# cambiano di rado: TTL breve + invalidazione esplicita sulle scritture.
_MAP_CACHE: Dict[Tuple[str, str], Tuple[float, Dict[str, str]]] = {}
_MAP_CACHE_LOCK = threading.Lock()
_MAP_CACHE_TTL_SECONDS = float(os.getenv("TRANSLATION_CACHE_TTL_SECONDS", "300"))


def invalidate_translation_cache() -> None:
    with _MAP_CACHE_LOCK:
        _MAP_CACHE.clear()


SUPPORTED_LANGUAGES = [
    {"code": "it", "label": "Italiano"},
    {"code": "en", "label": "English"},
    {"code": "fr", "label": "Francais"},
    {"code": "es", "label": "Espanol"},
    {"code": "pt", "label": "Portugues"},
]


def _platform_database_name() -> str:
    try:
        from tenant_config_repository import DB_NAME

        return str(DB_NAME or "").strip() or "APP_STOREHUB"
    except Exception:
        return (
            os.getenv("STOREHUB_TENANT_DATABASE")
            or os.getenv("SQLSERVER_DATABASE")
            or os.getenv("SQLSERVER_DB")
            or "APP_STOREHUB"
        )


def _connect_platform(read_only: bool = False):
    if get_backend() == "sqlserver":
        return get_connection_sqlserver_database(_platform_database_name(), read_only=read_only)
    return get_connection(None, read_only=read_only)


def _lang(value: str | None) -> str:
    code = str(value or "it").strip().lower()
    allowed = {x["code"] for x in SUPPORTED_LANGUAGES}
    return code if code in allowed else "it"


def _full_key(namespace: str, translation_key: str) -> str:
    ns = str(namespace or "common").strip()
    key = str(translation_key or "").strip()
    if key.startswith(f"{ns}."):
        return key
    return f"{ns}.{key}"


def _ensure_schema_on_connection(conn) -> None:
    cur = conn.cursor()
    cur.execute(
        """
IF OBJECT_ID('dbo.StoreHubTranslations', 'U') IS NULL
BEGIN
  CREATE TABLE dbo.StoreHubTranslations (
    row_uuid UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID() PRIMARY KEY,
    namespace NVARCHAR(100) NOT NULL,
    translation_key NVARCHAR(255) NOT NULL,
    language_code NVARCHAR(10) NOT NULL,
    source_text NVARCHAR(1000) NOT NULL,
    text_value NVARCHAR(1000) NOT NULL,
    auto_translated BIT NOT NULL DEFAULT 0,
    customized BIT NOT NULL DEFAULT 0,
    created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
  );
  CREATE UNIQUE INDEX UX_StoreHubTranslations_key_lang
    ON dbo.StoreHubTranslations(namespace, translation_key, language_code);
END
IF COL_LENGTH('dbo.StoreHubTranslations', 'auto_translated') IS NULL
  ALTER TABLE dbo.StoreHubTranslations ADD auto_translated BIT NOT NULL DEFAULT 0;
IF COL_LENGTH('dbo.StoreHubTranslations', 'customized') IS NULL
  ALTER TABLE dbo.StoreHubTranslations ADD customized BIT NOT NULL DEFAULT 0;
"""
    )
    conn.commit()


def ensure_translations_schema() -> None:
    conn = get_connection(None)
    try:
        _ensure_schema_on_connection(conn)
    finally:
        try:
            conn.close()
        except Exception:
            pass


def ensure_platform_translations_schema() -> None:
    conn = _connect_platform()
    try:
        _ensure_schema_on_connection(conn)
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _auto_translate(source_text: str, language_code: str) -> str:
    s = str(source_text or "").strip()
    code = _lang(language_code)
    if code == "it":
        return s
    known = {
        "Inventario": {"en": "Inventory", "fr": "Inventaire", "es": "Inventario"},
        "Inventario iniziale": {"en": "Initial inventory", "fr": "Inventaire initial", "es": "Inventario inicial"},
        "Inventario finale": {"en": "Final inventory", "fr": "Inventaire final", "es": "Inventario final"},
        "Trasferimenti In": {"en": "Transfers In", "fr": "Transferts entrants", "es": "Transferencias entrantes"},
        "Trasferimenti Out": {"en": "Transfers Out", "fr": "Transferts sortants", "es": "Transferencias salientes"},
        "Waste Crudo": {"en": "Raw Waste", "fr": "Dechets bruts", "es": "Desperdicio bruto"},
        "Waste crudo": {"en": "Raw waste", "fr": "Dechets bruts", "es": "Desperdicio bruto"},
        "Waste (crudo)": {"en": "Raw waste", "fr": "Dechets bruts", "es": "Desperdicio bruto"},
        "Consumo": {"en": "Consumption", "fr": "Consommation", "es": "Consumo"},
        "Consumo %": {"en": "Consumption %", "fr": "Consommation %", "es": "Consumo %"},
        "Waste %": {"en": "Waste %", "fr": "Dechets %", "es": "Desperdicio %"},
        "Dashboard": {"en": "Dashboard", "fr": "Tableau de bord", "es": "Panel"},
        "Cruscotto": {"en": "Control panel", "fr": "Pilotage", "es": "Panel de control"},
        "Magazzino": {"en": "Warehouse", "fr": "Stock", "es": "Almacen"},
        "Ordini al fornitore": {"en": "Supplier orders", "fr": "Commandes fournisseur", "es": "Pedidos a proveedor"},
        "Rendiconto": {"en": "Cash report", "fr": "Compte rendu caisse", "es": "Rendicion de caja"},
        "Gestione Orari": {"en": "Schedule management", "fr": "Gestion des horaires", "es": "Gestion de horarios"},
        "Link": {"en": "Links", "fr": "Liens", "es": "Enlaces"},
        "Controlli di gestione": {"en": "Management control", "fr": "Controle de gestion", "es": "Control de gestion"},
        "Estrazioni": {"en": "Exports", "fr": "Exports", "es": "Extracciones"},
        "Estrazioni HQ": {"en": "HQ exports", "fr": "Exports HQ", "es": "Extracciones HQ"},
        "Master": {"en": "Master", "fr": "Master", "es": "Master"},
        "Admin": {"en": "Admin", "fr": "Admin", "es": "Admin"},
        "Wizard nuovo tenant": {"en": "New tenant wizard", "fr": "Assistant nouveau tenant", "es": "Asistente nuevo tenant"},
        "Tenant": {"en": "Tenants", "fr": "Tenants", "es": "Tenants"},
        "Assegnazioni tenant": {"en": "Tenant assignments", "fr": "Affectations tenant", "es": "Asignaciones tenant"},
        "Utenti master": {"en": "Master users", "fr": "Utilisateurs master", "es": "Usuarios master"},
        "Admin tenant": {"en": "Tenant admin", "fr": "Admin tenant", "es": "Admin tenant"},
        "Traduzioni": {"en": "Translations", "fr": "Traductions", "es": "Traducciones"},
        "Configurazione orari": {"en": "Schedule configuration", "fr": "Configuration horaires", "es": "Configuracion de horarios"},
        "Configurazione distinta cassa": {"en": "Cash statement configuration", "fr": "Configuration caisse", "es": "Configuracion caja"},
        "Configurazione iPratico": {"en": "iPratico configuration", "fr": "Configuration iPratico", "es": "Configuracion iPratico"},
        "Configurazione delivery": {"en": "Delivery configuration", "fr": "Configuration delivery", "es": "Configuracion delivery"},
        "Import storico giornaliero": {"en": "Daily history import", "fr": "Import historique quotidien", "es": "Import historico diario"},
        "Pagine test": {"en": "Test pages", "fr": "Pages test", "es": "Paginas de prueba"},
        "Utenti": {"en": "Users", "fr": "Utilisateurs", "es": "Usuarios"},
        "Store": {"en": "Stores", "fr": "Stores", "es": "Tiendas"},
        "Area manager": {"en": "Area managers", "fr": "Area managers", "es": "Area managers"},
        "Fornitori": {"en": "Suppliers", "fr": "Fournisseurs", "es": "Proveedores"},
        "Anagrafica Listini Prezzi": {"en": "Price list registry", "fr": "Registre listes de prix", "es": "Registro listas de precios"},
        "Listini Prezzi": {"en": "Price lists", "fr": "Listes de prix", "es": "Listas de precios"},
        "Account": {"en": "Account", "fr": "Compte", "es": "Cuenta"},
        "Lingua": {"en": "Language", "fr": "Langue", "es": "Idioma"},
        "Logout": {"en": "Logout", "fr": "Deconnexion", "es": "Cerrar sesion"},
        "Attendere": {"en": "Please wait", "fr": "Veuillez patienter", "es": "Espera"},
        "Caricamento...": {"en": "Loading...", "fr": "Chargement...", "es": "Cargando..."},
        "Salvataggio in corso...": {"en": "Saving...", "fr": "Enregistrement...", "es": "Guardando..."},
        "Operazione in corso...": {"en": "Operation in progress...", "fr": "Operation en cours...", "es": "Operacion en curso..."},
        "Seleziona...": {"en": "Select...", "fr": "Selectionner...", "es": "Seleccionar..."},
        "Si": {"en": "Yes", "fr": "Oui", "es": "Si"},
        "No": {"en": "No", "fr": "Non", "es": "No"},
        "Crea": {"en": "Create", "fr": "Creer", "es": "Crear"},
        "Attivo": {"en": "Active", "fr": "Actif", "es": "Activo"},
        "Azioni": {"en": "Actions", "fr": "Actions", "es": "Acciones"},
        "Chiave": {"en": "Key", "fr": "Cle", "es": "Clave"},
        "Ordine": {"en": "Order", "fr": "Ordre", "es": "Orden"},
        "Ord.": {"en": "Ord.", "fr": "Ord.", "es": "Ord."},
        "Tutti": {"en": "All", "fr": "Tous", "es": "Todos"},
        "Nessuno": {"en": "None", "fr": "Aucun", "es": "Ninguno"},
        "Reset": {"en": "Reset", "fr": "Reinitialiser", "es": "Restablecer"},
        "Totale": {"en": "Total", "fr": "Total", "es": "Total"},
        "Periodo": {"en": "Period", "fr": "Periode", "es": "Periodo"},
        "Store": {"en": "Store", "fr": "Store", "es": "Store"},
        "Master - Configurazione delivery": {"en": "Master - Delivery configuration", "fr": "Master - Configuration delivery", "es": "Master - Configuracion delivery"},
        "Configurazione delivery": {"en": "Delivery configuration", "fr": "Configuration delivery", "es": "Configuracion delivery"},
        "Provider mostrati nella pagina Rendiconto - Gestione delivery. Il provider tecnico si seleziona dalle voci Delivery della distinta cassa.": {"en": "Providers shown on Cash report - Delivery management. The technical provider is selected from the Delivery entries in the cash statement.", "fr": "Providers affiches dans Caisse - Gestion delivery. Le provider technique est selectionne parmi les lignes Delivery de la caisse.", "es": "Providers mostrados en Caja - Gestion delivery. El provider tecnico se selecciona desde las voces Delivery de la caja."},
        "L'eliminazione rimuove solo la configurazione provider del tenant selezionato.": {"en": "Deleting removes only the provider configuration for the selected tenant.", "fr": "La suppression retire uniquement la configuration provider du tenant selectionne.", "es": "Eliminar quita solo la configuracion del provider del tenant seleccionado."},
        "Apri gestione delivery": {"en": "Open delivery management", "fr": "Ouvrir gestion delivery", "es": "Abrir gestion delivery"},
        "Nuovo provider": {"en": "New provider", "fr": "Nouveau provider", "es": "Nuevo provider"},
        "Provider distinta": {"en": "Statement provider", "fr": "Provider caisse", "es": "Provider caja"},
        "Logo file": {"en": "Logo file", "fr": "Fichier logo", "es": "Archivo logo"},
        "Numero": {"en": "Number", "fr": "Nombre", "es": "Numero"},
        "Percentuale": {"en": "Percentage", "fr": "Pourcentage", "es": "Porcentaje"},
        "Apertura": {"en": "Opening", "fr": "Ouverture", "es": "Apertura"},
        "Chiusura": {"en": "Closure", "fr": "Fermeture", "es": "Cierre"},
        "Etichetta apertura": {"en": "Opening label", "fr": "Libelle ouverture", "es": "Etiqueta apertura"},
        "% Apertura": {"en": "% Opening", "fr": "% Ouverture", "es": "% Apertura"},
        "(non presente in distinta)": {"en": "(not in statement)", "fr": "(absent de la caisse)", "es": "(no presente en caja)"},
        "Eliminare questo provider delivery dalla configurazione? I dati settimanali gia salvati non verranno cancellati.": {"en": "Delete this delivery provider from the configuration? Weekly data already saved will not be deleted.", "fr": "Supprimer ce provider delivery de la configuration ? Les donnees hebdomadaires deja enregistrees ne seront pas supprimees.", "es": "Eliminar este provider delivery de la configuracion? Los datos semanales ya guardados no se eliminaran."},
        "Nessun provider configurato.": {"en": "No provider configured.", "fr": "Aucun provider configure.", "es": "Ningun provider configurado."},
        "Dati Delivery": {"en": "Delivery data", "fr": "Donnees delivery", "es": "Datos delivery"},
        "Store visibili": {"en": "Visible stores", "fr": "Stores visibles", "es": "Stores visibles"},
        "Settimana da": {"en": "Week from", "fr": "Semaine du", "es": "Semana desde"},
        "Settimana a": {"en": "Week to", "fr": "Semaine au", "es": "Semana hasta"},
        "Store selezionati": {"en": "Selected stores", "fr": "Stores selectionnes", "es": "Stores seleccionados"},
        "Righe trovate": {"en": "Rows found", "fr": "Lignes trouvees", "es": "Filas encontradas"},
        "Cruscotto di analisi delivery": {"en": "Delivery analysis dashboard", "fr": "Tableau analyse delivery", "es": "Panel analisis delivery"},
        "Il riepilogo lavora sugli stessi dati filtrati nella tabella: incassi, rimborsi, recuperato e qualita del processo.": {"en": "The summary uses the same filtered data as the table: takings, refunds, recovered value and process quality.", "fr": "Le recapitulatif utilise les memes donnees filtrees que le tableau : encaissements, remboursements, recupere et qualite du processus.", "es": "El resumen usa los mismos datos filtrados de la tabla: cobros, reembolsos, recuperado y calidad del proceso."},
        "Righe analizzate": {"en": "Rows analyzed", "fr": "Lignes analysees", "es": "Filas analizadas"},
        "Pagamento Online": {"en": "Online payment", "fr": "Paiement en ligne", "es": "Pago online"},
        "Pagamento contanti": {"en": "Cash payment", "fr": "Paiement especes", "es": "Pago efectivo"},
        "Rimborsi richiesti": {"en": "Refunds requested", "fr": "Remboursements demandes", "es": "Reembolsos solicitados"},
        "Valore rimborsi": {"en": "Refund value", "fr": "Valeur remboursements", "es": "Valor reembolsos"},
        "Rimborsi contestati": {"en": "Refunds disputed", "fr": "Remboursements contestes", "es": "Reembolsos reclamados"},
        "Contestazioni accettate": {"en": "Accepted disputes", "fr": "Contestations acceptees", "es": "Reclamaciones aceptadas"},
        "Valore recuperato": {"en": "Recovered value", "fr": "Valeur recuperee", "es": "Valor recuperado"},
        "Negozi compilati": {"en": "Completed stores", "fr": "Stores completes", "es": "Stores completados"},
        "negozi compilati": {"en": "completed stores", "fr": "stores completes", "es": "stores completados"},
        "Composizione incassi": {"en": "Payment composition", "fr": "Composition encaissements", "es": "Composicion cobros"},
        "Totale incassato": {"en": "Total collected", "fr": "Total encaisse", "es": "Total cobrado"},
        "dei rimborsi": {"en": "of refunds", "fr": "des remboursements", "es": "de los reembolsos"},
        "Le percentuali sono calcolate sul totale dei dati filtrati e visualizzati nella tabella.": {"en": "Percentages are calculated on the total filtered data shown in the table.", "fr": "Les pourcentages sont calcules sur le total des donnees filtrees affichees dans le tableau.", "es": "Los porcentajes se calculan sobre el total de datos filtrados mostrados en la tabla."},
        "Funnel rimborsi": {"en": "Refund funnel", "fr": "Funnel remboursements", "es": "Embudo reembolsos"},
        "Richieste complessive": {"en": "Total requests", "fr": "Demandes totales", "es": "Solicitudes totales"},
        "sulle richieste": {"en": "on requests", "fr": "sur les demandes", "es": "sobre solicitudes"},
        "Settimana": {"en": "Week", "fr": "Semaine", "es": "Semana"},
        "Contanti": {"en": "Cash", "fr": "Especes", "es": "Efectivo"},
        "Online": {"en": "Online", "fr": "En ligne", "es": "Online"},
        "Ordini": {"en": "Orders", "fr": "Commandes", "es": "Pedidos"},
        "Cancellati": {"en": "Cancelled", "fr": "Annules", "es": "Cancelados"},
        "Rimborsi": {"en": "Refunds", "fr": "Remboursements", "es": "Reembolsos"},
        "Contestati": {"en": "Disputed", "fr": "Contestes", "es": "Reclamados"},
        "Accettati": {"en": "Accepted", "fr": "Acceptes", "es": "Aceptados"},
        "Rimb. annullati": {"en": "Cancelled refunds", "fr": "Remb. annules", "es": "Reemb. anulados"},
        "Apertura %": {"en": "Opening %", "fr": "Ouverture %", "es": "Apertura %"},
        "Nessun dato trovato con i filtri selezionati.": {"en": "No data found with the selected filters.", "fr": "Aucune donnee trouvee avec les filtres selectionnes.", "es": "No se encontraron datos con los filtros seleccionados."},
        "Tutti gli store visibili": {"en": "All visible stores", "fr": "Tous les stores visibles", "es": "Todos los stores visibles"},
        "1 store selezionato": {"en": "1 store selected", "fr": "1 store selectionne", "es": "1 store seleccionado"},
        "store selezionati": {"en": "stores selected", "fr": "stores selectionnes", "es": "stores seleccionados"},
        "Store attivo:": {"en": "Active store:", "fr": "Store actif :", "es": "Store activo:"},
        "Nessuno store selezionato": {"en": "No store selected", "fr": "Aucun store selectionne", "es": "Ningun store seleccionado"},
        "Cambia store": {"en": "Change store", "fr": "Changer store", "es": "Cambiar store"},
        "Distinta cassa": {"en": "Cash statement", "fr": "Caisse", "es": "Caja"},
        "Dati chiusura": {"en": "Closing data", "fr": "Donnees de cloture", "es": "Datos de cierre"},
        "Foto distinta": {"en": "Cash statement photo", "fr": "Photo caisse", "es": "Foto de caja"},
        "Foto": {"en": "Photo", "fr": "Photo", "es": "Foto"},
        "Distinte": {"en": "Cash deposits", "fr": "Remises especes", "es": "Entregas de efectivo"},
        "Ticket": {"en": "Meal vouchers", "fr": "Tickets repas", "es": "Tickets restaurante"},
        "Delivery": {"en": "Delivery", "fr": "Delivery", "es": "Delivery"},
        "Coupon": {"en": "Coupons", "fr": "Coupons", "es": "Cupones"},
        "VENDITE LORDE": {"en": "Gross sales", "fr": "Ventes brutes", "es": "Ventas brutas"},
        "ANNULLATI": {"en": "Cancellations", "fr": "Annulations", "es": "Anulados"},
        "SCONTRINI": {"en": "Receipts", "fr": "Tickets", "es": "Tickets"},
        "POS": {"en": "Card payments", "fr": "Paiements carte", "es": "Pagos con tarjeta"},
        "CONTANTI": {"en": "Cash", "fr": "Especes", "es": "Efectivo"},
        "TICKET": {"en": "Meal vouchers", "fr": "Tickets repas", "es": "Tickets restaurante"},
        "FATTURE": {"en": "Invoices", "fr": "Factures", "es": "Facturas"},
        "NUMERO FATTURE": {"en": "Invoice count", "fr": "Nombre de factures", "es": "Numero de facturas"},
        "OMAGGI": {"en": "Complimentary items", "fr": "Offerts", "es": "Invitaciones"},
        "VENDITE IVA 4%": {"en": "Sales VAT 4%", "fr": "Ventes TVA 4%", "es": "Ventas IVA 4%"},
        "VENDITE IVA 22%": {"en": "Sales VAT 22%", "fr": "Ventes TVA 22%", "es": "Ventas IVA 22%"},
        "GIRO AFFARI": {"en": "Turnover", "fr": "Chiffre d'affaires", "es": "Volumen de negocio"},
        "SPESE": {"en": "Expenses", "fr": "Depenses", "es": "Gastos"},
        "DIFFERENZA CASSA": {"en": "Cash difference", "fr": "Ecart de caisse", "es": "Diferencia de caja"},
        "Data": {"en": "Date", "fr": "Date", "es": "Fecha"},
        "Import iPratico": {"en": "Import iPratico", "fr": "Import iPratico", "es": "Importar iPratico"},
        "Dato importato": {"en": "Imported value", "fr": "Valeur importee", "es": "Dato importado"},
        "Spese": {"en": "Expenses", "fr": "Depenses", "es": "Gastos"},
        "Note credito": {"en": "Credit notes", "fr": "Avoirs", "es": "Notas de credito"},
        "Tipo": {"en": "Type", "fr": "Type", "es": "Tipo"},
        "Importo": {"en": "Amount", "fr": "Montant", "es": "Importe"},
        "Voce": {"en": "Item", "fr": "Ligne", "es": "Concepto"},
        "Valore": {"en": "Value", "fr": "Valeur", "es": "Valor"},
        "Totale": {"en": "Total", "fr": "Total", "es": "Total"},
        "Taglio": {"en": "Denomination", "fr": "Coupure", "es": "Denominacion"},
        "Qta": {"en": "Qty", "fr": "Qte", "es": "Cant."},
        "Monete totali": {"en": "Coins total", "fr": "Total pieces", "es": "Total monedas"},
        "Seleziona...": {"en": "Select...", "fr": "Selectionner...", "es": "Selecciona..."},
        "ONLINE": {"en": "ONLINE", "fr": "EN LIGNE", "es": "ONLINE"},
        "Elimina distinta": {"en": "Delete statement", "fr": "Supprimer caisse", "es": "Eliminar caja"},
        "Salva": {"en": "Save", "fr": "Enregistrer", "es": "Guardar"},
        "Aggiungi": {"en": "Add", "fr": "Ajouter", "es": "Anadir"},
        "Attiva": {"en": "Activate", "fr": "Activer", "es": "Activar"},
        "Disattiva": {"en": "Deactivate", "fr": "Desactiver", "es": "Desactivar"},
        "Attivo": {"en": "Active", "fr": "Actif", "es": "Activo"},
        "Inattivo": {"en": "Inactive", "fr": "Inactif", "es": "Inactivo"},
        "Azioni": {"en": "Actions", "fr": "Actions", "es": "Acciones"},
        "Stato": {"en": "Status", "fr": "Statut", "es": "Estado"},
        "Nome": {"en": "Name", "fr": "Nom", "es": "Nombre"},
        "Nome e Cognome": {"en": "Full name", "fr": "Nom et prenom", "es": "Nombre y apellidos"},
        "Anagrafica": {"en": "Registry", "fr": "Registre", "es": "Anagrafica"},
        "Anagrafica Staff": {"en": "Staff registry", "fr": "Registre staff", "es": "Anagrafica staff"},
        "Inquadramento": {"en": "Employment type", "fr": "Classification", "es": "Categoria laboral"},
        "Ore Contrattuali": {"en": "Contract hours", "fr": "Heures contractuelles", "es": "Horas contractuales"},
        "Codice Dipendente": {"en": "Employee code", "fr": "Code salarie", "es": "Codigo empleado"},
        "Scheduling": {"en": "Scheduling", "fr": "Planning", "es": "Planificacion"},
        "Seleziona...": {"en": "Select...", "fr": "Selectionner...", "es": "Selecciona..."},
        "Seleziona...": {"en": "Select...", "fr": "Selectionner...", "es": "Selecciona..."},
        "Applica": {"en": "Apply", "fr": "Appliquer", "es": "Aplicar"},
        "Settimana": {"en": "Week", "fr": "Semaine", "es": "Semana"},
        "Periodo": {"en": "Period", "fr": "Periode", "es": "Periodo"},
        "Intervallo": {"en": "Range", "fr": "Intervalle", "es": "Intervalo"},
        "Per fornitore": {"en": "By supplier", "fr": "Par fournisseur", "es": "Por proveedor"},
        "Fornitore": {"en": "Supplier", "fr": "Fournisseur", "es": "Proveedor"},
        "Dati Magazzino": {"en": "Warehouse data", "fr": "Donnees stock", "es": "Datos de almacen"},
        "Riepilogo mensile (valori in euro)": {"en": "Monthly summary (values in euros)", "fr": "Resume mensuel (valeurs en euros)", "es": "Resumen mensual (valores en euros)"},
        "Listino": {"en": "Price list", "fr": "Liste de prix", "es": "Lista de precios"},
        "Tabella riepilogativa": {"en": "Summary table", "fr": "Tableau recapitulatif", "es": "Tabla resumen"},
        "Revenues (net) totali": {"en": "Total revenues (net)", "fr": "Revenus (net) totaux", "es": "Ingresos (neto) totales"},
        "Esporta Excel": {"en": "Export Excel", "fr": "Exporter Excel", "es": "Exportar Excel"},
        "Seleziona mese e listino, poi premi \"Applica\".": {"en": "Select month and price list, then press \"Apply\".", "fr": "Selectionnez le mois et la liste de prix, puis appuyez sur \"Appliquer\".", "es": "Selecciona mes y lista de precios, luego pulsa \"Aplicar\"."},
        "Consumi": {"en": "Consumption", "fr": "Consommations", "es": "Consumos"},
        "Consumo in pezzi per prodotto": {"en": "Consumption in pieces by product", "fr": "Consommation en pieces par produit", "es": "Consumo en piezas por producto"},
        "Settimana (lun - dom)": {"en": "Week (Mon - Sun)", "fr": "Semaine (lun - dim)", "es": "Semana (lun - dom)"},
        "Mese": {"en": "Month", "fr": "Mois", "es": "Mes"},
        "Revenues (net) periodo": {"en": "Period revenues (net)", "fr": "Revenus (net) periode", "es": "Ingresos (neto) periodo"},
        "Dettaglio prodotti": {"en": "Product detail", "fr": "Detail produits", "es": "Detalle productos"},
        "Tocca un prodotto per vedere i dettagli.": {"en": "Tap a product to view details.", "fr": "Touchez un produit pour voir les details.", "es": "Toca un producto para ver los detalles."},
        "Mostra dettagli": {"en": "Show details", "fr": "Afficher details", "es": "Mostrar detalles"},
        "Nascondi dettagli": {"en": "Hide details", "fr": "Masquer details", "es": "Ocultar detalles"},
        "Descrizione": {"en": "Description", "fr": "Description", "es": "Descripcion"},
        "Nessun dato per i filtri selezionati.": {"en": "No data for the selected filters.", "fr": "Aucune donnee pour les filtres selectionnes.", "es": "No hay datos para los filtros seleccionados."},
        "Seleziona un fornitore e premi \"Applica\".": {"en": "Select a supplier and press \"Apply\".", "fr": "Selectionnez un fournisseur et appuyez sur \"Appliquer\".", "es": "Selecciona un proveedor y pulsa \"Aplicar\"."},
        "Seleziona un periodo, un fornitore e premi \"Applica\".": {"en": "Select a period and supplier, then press \"Apply\".", "fr": "Selectionnez une periode et un fournisseur, puis appuyez sur \"Appliquer\".", "es": "Selecciona un periodo y un proveedor, luego pulsa \"Aplicar\"."},
        "Inserimento DDT": {"en": "DDT entry", "fr": "Saisie DDT", "es": "Entrada DDT"},
        "Modifica DDT": {"en": "Edit DDT", "fr": "Modifier DDT", "es": "Modificar DDT"},
        "Data documento": {"en": "Document date", "fr": "Date document", "es": "Fecha documento"},
        "Data consegna": {"en": "Delivery date", "fr": "Date livraison", "es": "Fecha entrega"},
        "Seleziona un fornitore...": {"en": "Select a supplier...", "fr": "Selectionner un fournisseur...", "es": "Selecciona un proveedor..."},
        "Carica listino": {"en": "Load price list", "fr": "Charger liste de prix", "es": "Cargar lista de precios"},
        "Cerca prodotto (descrizione)...": {"en": "Search product (description)...", "fr": "Rechercher produit (description)...", "es": "Buscar producto (descripcion)..."},
        "Totale parziale inserimento": {"en": "Partial entry total", "fr": "Total partiel saisie", "es": "Total parcial entrada"},
        "Totale parziale DDT": {"en": "Partial DDT total", "fr": "Total partiel DDT", "es": "Total parcial DDT"},
        "Tot EUR": {"en": "Total EUR", "fr": "Total EUR", "es": "Total EUR"},
        "Gruppo": {"en": "Group", "fr": "Groupe", "es": "Grupo"},
        "Nessun importo inserito": {"en": "No amount entered", "fr": "Aucun montant saisi", "es": "Ningun importe introducido"},
        "Nessun prodotto trovato": {"en": "No product found", "fr": "Aucun produit trouve", "es": "No se encontro ningun producto"},
        "Colli": {"en": "Packages", "fr": "Colis", "es": "Bultos"},
        "Pezzi": {"en": "Pieces", "fr": "Pieces", "es": "Piezas"},
        "Pezzi/Kg": {"en": "Pieces/Kg", "fr": "Pieces/Kg", "es": "Piezas/Kg"},
        "Prezzo DDT": {"en": "DDT price", "fr": "Prix DDT", "es": "Precio DDT"},
        "Sconto": {"en": "Discount", "fr": "Remise", "es": "Descuento"},
        "Sconto %": {"en": "Discount %", "fr": "Remise %", "es": "Descuento %"},
        "Q.tà totale": {"en": "Total qty", "fr": "Qte totale", "es": "Cant. total"},
        "Valore": {"en": "Value", "fr": "Valeur", "es": "Valor"},
        "Ripristina prezzo di listino": {"en": "Restore list price", "fr": "Restaurer prix liste", "es": "Restaurar precio lista"},
        "Prodotto": {"en": "Product", "fr": "Produit", "es": "Producto"},
        "Prezzo listino": {"en": "List price", "fr": "Prix liste", "es": "Precio lista"},
        "Unità": {"en": "Unit", "fr": "Unite", "es": "Unidad"},
        "I campi da compilare per il DDT sono: Colli, Pezzi (se necessario), Prezzo DDT (se diverso dal listino), Sconto %. Le colonne quantità totale e valore vengono calcolate automaticamente.": {"en": "Fields to fill for the DDT: packages, pieces if needed, DDT price if different from the list price, discount %. Total quantity and value are calculated automatically.", "fr": "Champs a remplir pour le DDT : colis, pieces si necessaire, prix DDT si different du prix liste, remise %. La quantite totale et la valeur sont calculees automatiquement.", "es": "Campos a completar para el DDT: bultos, piezas si hace falta, precio DDT si difiere de la lista, descuento %. La cantidad total y el valor se calculan automaticamente."},
        "Nessun articolo trovato per il fornitore selezionato.": {"en": "No item found for the selected supplier.", "fr": "Aucun article trouve pour le fournisseur selectionne.", "es": "No se encontro ningun articulo para el proveedor seleccionado."},
        "Righe trovate": {"en": "Rows found", "fr": "Lignes trouvees", "es": "Filas encontradas"},
        "Modifica colli/pezzi e premi Salva modifiche. Le righe eliminate con la X vengono cancellate subito.": {"en": "Edit packages/pieces and press Save changes. Rows deleted with X are removed immediately.", "fr": "Modifiez colis/pieces et appuyez sur Enregistrer. Les lignes supprimees avec X sont effacees immediatement.", "es": "Modifica bultos/piezas y pulsa Guardar. Las filas eliminadas con X se borran inmediatamente."},
        "Cambio date per tutto il DDT": {"en": "Change dates for the whole DDT", "fr": "Changer les dates pour tout le DDT", "es": "Cambiar fechas para todo el DDT"},
        "Nuova data documento (Fattura)": {"en": "New document date (Invoice)", "fr": "Nouvelle date document (Facture)", "es": "Nueva fecha documento (Factura)"},
        "Nuova data consegna (Data)": {"en": "New delivery date (Date)", "fr": "Nouvelle date livraison (Date)", "es": "Nueva fecha entrega (Fecha)"},
        "Applica cambio date": {"en": "Apply date change", "fr": "Appliquer changement dates", "es": "Aplicar cambio de fechas"},
        "Nessuna riga trovata per i parametri inseriti.": {"en": "No rows found for the entered parameters.", "fr": "Aucune ligne trouvee pour les parametres saisis.", "es": "No se encontraron filas para los parametros introducidos."},
        "Qta (calcolata)": {"en": "Qty (calculated)", "fr": "Qte (calculee)", "es": "Cant. (calculada)"},
        "Descrizione": {"en": "Description", "fr": "Description", "es": "Descripcion"},
        "Prezzo": {"en": "Price", "fr": "Prix", "es": "Precio"},
        "Qta car": {"en": "Case qty", "fr": "Qte carton", "es": "Cant. caja"},
        "Qta int": {"en": "Inner qty", "fr": "Qte interne", "es": "Cant. interna"},
        "(Senza gruppo)": {"en": "(No group)", "fr": "(Sans groupe)", "es": "(Sin grupo)"},
        "(Senza descrizione)": {"en": "(No description)", "fr": "(Sans description)", "es": "(Sin descripcion)"},
        "fattore": {"en": "factor", "fr": "facteur", "es": "factor"},
        "Compila \"Data documento\" e \"Data consegna\" prima di salvare il DDT.": {"en": "Fill in \"Document date\" and \"Delivery date\" before saving the DDT.", "fr": "Renseignez \"Date document\" et \"Date livraison\" avant d'enregistrer le DDT.", "es": "Completa \"Fecha documento\" y \"Fecha entrega\" antes de guardar el DDT."},
        "Valore \"Colli\" non valido nella riga:": {"en": "Invalid \"Packages\" value in row:", "fr": "Valeur \"Colis\" non valide a la ligne :", "es": "Valor \"Bultos\" no valido en la fila:"},
        "Valore \"Pezzi\" non valido nella riga:": {"en": "Invalid \"Pieces\" value in row:", "fr": "Valeur \"Pieces\" non valide a la ligne :", "es": "Valor \"Piezas\" no valido en la fila:"},
        "Usa solo numeri (decimali con , o .).": {"en": "Use numbers only (decimals with , or .).", "fr": "Utilisez uniquement des nombres (decimales avec , ou .).", "es": "Usa solo numeros (decimales con , o .)."},
        "Riga": {"en": "Row", "fr": "Ligne", "es": "Fila"},
        "Non hai inserito quantità su nessuna riga. Vuoi proseguire comunque con il salvataggio?": {"en": "You have not entered quantities on any row. Do you still want to save?", "fr": "Vous n'avez saisi aucune quantite. Voulez-vous quand meme enregistrer ?", "es": "No has introducido cantidades en ninguna fila. Quieres guardar igualmente?"},
        "Stai per salvare il seguente DDT:": {"en": "You are about to save this DDT:", "fr": "Vous allez enregistrer ce DDT :", "es": "Vas a guardar este DDT:"},
        "Righe compilate": {"en": "Filled rows", "fr": "Lignes remplies", "es": "Filas completadas"},
        "Confermi il salvataggio?": {"en": "Confirm save?", "fr": "Confirmer l'enregistrement ?", "es": "Confirmas el guardado?"},
        "Confermi?": {"en": "Confirm?", "fr": "Confirmer ?", "es": "Confirmas?"},
        "Dati mancanti per eliminare la riga.": {"en": "Missing data to delete the row.", "fr": "Donnees manquantes pour supprimer la ligne.", "es": "Faltan datos para eliminar la fila."},
        "Confermi eliminazione della riga": {"en": "Confirm deletion of row", "fr": "Confirmer suppression de la ligne", "es": "Confirmas eliminar la fila"},
        "Errore eliminazione riga.": {"en": "Row deletion error.", "fr": "Erreur suppression ligne.", "es": "Error al eliminar la fila."},
        "Errore rete durante eliminazione:": {"en": "Network error during deletion:", "fr": "Erreur reseau pendant la suppression :", "es": "Error de red durante la eliminacion:"},
        "Compila fornitore e date prima di salvare.": {"en": "Fill in supplier and dates before saving.", "fr": "Renseignez fournisseur et dates avant d'enregistrer.", "es": "Completa proveedor y fechas antes de guardar."},
        "Stai per salvare le modifiche del DDT:": {"en": "You are about to save DDT changes:", "fr": "Vous allez enregistrer les modifications du DDT :", "es": "Vas a guardar los cambios del DDT:"},
        "Righe modificate": {"en": "Changed rows", "fr": "Lignes modifiees", "es": "Filas modificadas"},
        "Nessuna riga modificata. Vuoi procedere comunque?": {"en": "No row changed. Do you want to continue anyway?", "fr": "Aucune ligne modifiee. Voulez-vous continuer ?", "es": "No hay filas modificadas. Quieres continuar igualmente?"},
        "Compila le nuove date prima di applicare.": {"en": "Fill in the new dates before applying.", "fr": "Renseignez les nouvelles dates avant d'appliquer.", "es": "Completa las nuevas fechas antes de aplicar."},
        "Confermi cambio date su tutte le righe del DDT?": {"en": "Confirm date change on all DDT rows?", "fr": "Confirmer le changement de dates sur toutes les lignes du DDT ?", "es": "Confirmas el cambio de fechas en todas las filas del DDT?"},
        "Nessun risultato.": {"en": "No results.", "fr": "Aucun resultat.", "es": "Sin resultados."},
        "Apri": {"en": "Open", "fr": "Ouvrir", "es": "Abrir"},
        "Seleziona un periodo e premi \"Applica\".": {"en": "Select a period and press \"Apply\".", "fr": "Selectionnez une periode et appuyez sur \"Appliquer\".", "es": "Selecciona un periodo y pulsa \"Aplicar\"."},
        "Seleziona un fornitore per vedere i KPI dedicati.": {"en": "Select a supplier to view dedicated KPIs.", "fr": "Selectionnez un fournisseur pour voir les KPI dedies.", "es": "Selecciona un proveedor para ver los KPI dedicados."},
        "Errore": {"en": "Error", "fr": "Erreur", "es": "Error"},
        "Tipo periodo": {"en": "Period type", "fr": "Type de periode", "es": "Tipo de periodo"},
        "Seleziona una data": {"en": "Select a date", "fr": "Selectionner une date", "es": "Selecciona una fecha"},
        "Seleziona mese": {"en": "Select month", "fr": "Selectionner le mois", "es": "Selecciona mes"},
        "Totale store": {"en": "Store total", "fr": "Total store", "es": "Total tienda"},
        "Revenues (net)": {"en": "Revenues (net)", "fr": "Revenus (net)", "es": "Ingresos (neto)"},
        "Tutte": {"en": "All", "fr": "Toutes", "es": "Todas"},
        "Tipo movimentazione": {"en": "Movement type", "fr": "Type de mouvement", "es": "Tipo de movimiento"},
        "Ricerca Magazzino": {"en": "Warehouse search", "fr": "Recherche stock", "es": "Busqueda almacen"},
        "Ricerca movimentazioni": {"en": "Movement search", "fr": "Recherche mouvements", "es": "Busqueda movimientos"},
        "Dettagli": {"en": "Details", "fr": "Details", "es": "Detalles"},
        "Trasferimento IN": {"en": "Transfer IN", "fr": "Transfert IN", "es": "Transferencia IN"},
        "Trasferimento OUT": {"en": "Transfer OUT", "fr": "Transfert OUT", "es": "Transferencia OUT"},
        "Intervallo non valido: la data di fine e precedente alla data di inizio.": {"en": "Invalid range: the end date is before the start date.", "fr": "Intervalle non valide : la date de fin precede la date de debut.", "es": "Intervalo no valido: la fecha final es anterior a la fecha inicial."},
        "Intervallo troppo ampio: la ricerca e limitata a massimo 1 mese.": {"en": "Range too wide: search is limited to a maximum of 1 month.", "fr": "Intervalle trop large : la recherche est limitee a 1 mois maximum.", "es": "Intervalo demasiado amplio: la busqueda esta limitada a maximo 1 mes."},
        "Legenda colori (Orari)": {"en": "Color legend (Schedules)", "fr": "Legende couleurs (Horaires)", "es": "Leyenda de colores (Horarios)"},
        "Crea una legenda per i colori utilizzati nella pagina Orari.": {"en": "Create a legend for the colors used on the Schedules page.", "fr": "Creez une legende pour les couleurs utilisees dans la page Horaires.", "es": "Crea una leyenda para los colores utilizados en la pagina Horarios."},
        "Nome legenda": {"en": "Legend name", "fr": "Nom de la legende", "es": "Nombre de la leyenda"},
        "Colore": {"en": "Color", "fr": "Couleur", "es": "Color"},
        "Scegli colore": {"en": "Choose color", "fr": "Choisir couleur", "es": "Elegir color"},
        "Seleziona colore legenda": {"en": "Select legend color", "fr": "Selectionner couleur de legende", "es": "Seleccionar color de leyenda"},
        "Scegli uno dei colori disponibili nella pagina Orari.": {"en": "Choose one of the colors available on the Schedules page.", "fr": "Choisissez une des couleurs disponibles dans la page Horaires.", "es": "Elige uno de los colores disponibles en la pagina Horarios."},
        "Salva legenda": {"en": "Save legend", "fr": "Enregistrer legende", "es": "Guardar leyenda"},
        "Nessuna persona presente.": {"en": "No people found.", "fr": "Aucune personne presente.", "es": "No hay personas."},
        "Nessuna legenda presente.": {"en": "No legend found.", "fr": "Aucune legende presente.", "es": "No hay leyendas."},
        "Eliminare definitivamente questa persona?": {"en": "Delete this person permanently?", "fr": "Supprimer definitivement cette personne ?", "es": "Eliminar definitivamente esta persona?"},
        "Eliminare questa legenda?": {"en": "Delete this legend?", "fr": "Supprimer cette legende ?", "es": "Eliminar esta leyenda?"},
        "Gestione Orari - Orari": {"en": "Schedule management - Schedules", "fr": "Gestion des horaires - Horaires", "es": "Gestion de horarios - Horarios"},
        "Persone": {"en": "People", "fr": "Personnes", "es": "Personas"},
        "Cerca...": {"en": "Search...", "fr": "Rechercher...", "es": "Buscar..."},
        "Tutti": {"en": "All", "fr": "Tous", "es": "Todos"},
        "Nessuno": {"en": "None", "fr": "Aucun", "es": "Ninguno"},
        "Settimana corrente": {"en": "Current week", "fr": "Semaine courante", "es": "Semana actual"},
        "Importa settimana": {"en": "Import week", "fr": "Importer semaine", "es": "Importar semana"},
        "Sovrascrive gli orari della settimana corrente": {"en": "Overwrites the schedules of the current week", "fr": "Ecrase les horaires de la semaine courante", "es": "Sobrescribe los horarios de la semana actual"},
        "Mostra lineare": {"en": "Show linear", "fr": "Afficher lineaire", "es": "Mostrar lineal"},
        "Fatturato totale settimana": {"en": "Total weekly revenue", "fr": "Chiffre d'affaires hebdomadaire total", "es": "Facturacion total semanal"},
        "Ore totali": {"en": "Total hours", "fr": "Heures totales", "es": "Horas totales"},
        "Produttivita": {"en": "Productivity", "fr": "Productivite", "es": "Productividad"},
        "Legenda colori:": {"en": "Color legend:", "fr": "Legende couleurs :", "es": "Leyenda de colores:"},
        "Attenzione": {"en": "Warning", "fr": "Attention", "es": "Atencion"},
        "Anomalie ore contrattuali": {"en": "Contract hours anomalies", "fr": "Anomalies heures contractuelles", "es": "Anomalias horas contractuales"},
        "Modifiche non salvate": {"en": "Unsaved changes", "fr": "Modifications non enregistrees", "es": "Cambios no guardados"},
        "Ci sono modifiche non salvate. Vuoi salvarle prima di generare il PDF?": {"en": "There are unsaved changes. Do you want to save them before generating the PDF?", "fr": "Des modifications ne sont pas enregistrees. Voulez-vous les enregistrer avant de generer le PDF ?", "es": "Hay cambios no guardados. Quieres guardarlos antes de generar el PDF?"},
        "Genera senza salvare": {"en": "Generate without saving", "fr": "Generer sans enregistrer", "es": "Generar sin guardar"},
        "Salva e genera PDF": {"en": "Save and generate PDF", "fr": "Enregistrer et generer PDF", "es": "Guardar y generar PDF"},
        "Seleziona una data nella settimana da copiare. Gli orari della settimana corrente verranno sovrascritti.": {"en": "Select a date in the week to copy. The current week schedules will be overwritten.", "fr": "Selectionnez une date de la semaine a copier. Les horaires de la semaine courante seront ecrases.", "es": "Selecciona una fecha de la semana a copiar. Los horarios de la semana actual se sobrescribiran."},
        "Settimana sorgente": {"en": "Source week", "fr": "Semaine source", "es": "Semana origen"},
        "Importa": {"en": "Import", "fr": "Importer", "es": "Importar"},
        "Colore cella": {"en": "Cell color", "fr": "Couleur cellule", "es": "Color celda"},
        "Copia giornata": {"en": "Copy day", "fr": "Copier journee", "es": "Copiar dia"},
        "Incolla": {"en": "Paste", "fr": "Coller", "es": "Pegar"},
        "Svuota copia": {"en": "Clear copy", "fr": "Vider copie", "es": "Vaciar copia"},
        "Lineare": {"en": "Linear", "fr": "Lineaire", "es": "Lineal"},
        "Giorno": {"en": "Day", "fr": "Jour", "es": "Dia"},
        "Previsione netta": {"en": "Net forecast", "fr": "Prevision nette", "es": "Prevision neta"},
        "Previsione netta settimana": {"en": "Weekly net forecast", "fr": "Prevision nette semaine", "es": "Prevision neta semanal"},
        "Previsione": {"en": "Forecast", "fr": "Prevision", "es": "Prevision"},
        "Proiezione": {"en": "Projection", "fr": "Projection", "es": "Proyeccion"},
        "Prev.": {"en": "Prev.", "fr": "Prec.", "es": "Ant."},
        "Previsioni": {"en": "Forecasts", "fr": "Previsions", "es": "Previsiones"},
        "Anno precedente netto": {"en": "Previous year net", "fr": "Annee precedente nette", "es": "Ano anterior neto"},
        "Anno prec.": {"en": "Prev. year", "fr": "Annee prec.", "es": "Ano ant."},
        "Allineato a": {"en": "Aligned to", "fr": "Aligne sur", "es": "Alineado a"},
        "Nominativo": {"en": "Name", "fr": "Nom", "es": "Nombre"},
        "Seleziona almeno una persona.": {"en": "Select at least one person.", "fr": "Selectionnez au moins une personne.", "es": "Selecciona al menos una persona."},
        "Errore caricamento.": {"en": "Loading error.", "fr": "Erreur de chargement.", "es": "Error de carga."},
        "Campo obbligatorio": {"en": "Required field", "fr": "Champ obligatoire", "es": "Campo obligatorio"},
        "Inserisci la previsione vendite per tutti i giorni della settimana.": {"en": "Enter the sales forecast for every day of the week.", "fr": "Saisissez la prevision de ventes pour chaque jour de la semaine.", "es": "Introduce la prevision de ventas para todos los dias de la semana."},
        "Dati settimana non disponibili.": {"en": "Week data unavailable.", "fr": "Donnees semaine non disponibles.", "es": "Datos de la semana no disponibles."},
        "Colore riga": {"en": "Row color", "fr": "Couleur ligne", "es": "Color de fila"},
        "Causale": {"en": "Reason", "fr": "Motif", "es": "Causa"},
        "Store prestito": {"en": "Loan store", "fr": "Store pret", "es": "Tienda prestamo"},
        "LUN": {"en": "MON", "fr": "LUN", "es": "LUN"},
        "MAR": {"en": "TUE", "fr": "MAR", "es": "MAR"},
        "MER": {"en": "WED", "fr": "MER", "es": "MIE"},
        "GIO": {"en": "THU", "fr": "JEU", "es": "JUE"},
        "VEN": {"en": "FRI", "fr": "VEN", "es": "VIE"},
        "SAB": {"en": "SAT", "fr": "SAM", "es": "SAB"},
        "DOM": {"en": "SUN", "fr": "DIM", "es": "DOM"},
        "Analisi Settimanale": {"en": "Weekly analysis", "fr": "Analyse hebdomadaire", "es": "Analisis semanal"},
        "Analisi Mensile": {"en": "Monthly analysis", "fr": "Analyse mensuelle", "es": "Analisis mensual"},
        "Cruscotto - Analisi Settimanale": {"en": "Control panel - Weekly analysis", "fr": "Pilotage - Analyse hebdomadaire", "es": "Panel de control - Analisis semanal"},
        "Cruscotto - Analisi Mensile": {"en": "Control panel - Monthly analysis", "fr": "Pilotage - Analyse mensuelle", "es": "Panel de control - Analisis mensual"},
        "Seleziona anno": {"en": "Select year", "fr": "Selectionner annee", "es": "Selecciona ano"},
        "Seleziona settimana": {"en": "Select week", "fr": "Selectionner semaine", "es": "Selecciona semana"},
        "Seleziona mese": {"en": "Select month", "fr": "Selectionner mois", "es": "Selecciona mes"},
        "Panoramica": {"en": "Overview", "fr": "Vue d'ensemble", "es": "Resumen"},
        "Dettaglio settimana": {"en": "Week detail", "fr": "Detail semaine", "es": "Detalle semana"},
        "Dettaglio mese": {"en": "Month detail", "fr": "Detail mois", "es": "Detalle mes"},
        "Grafici": {"en": "Charts", "fr": "Graphiques", "es": "Graficos"},
        "Actual (o previsione se mancante) vs Last Year vs Budget": {"en": "Actual (or forecast if missing) vs Last Year vs Budget", "fr": "Actual (ou prevision si manquant) vs Last Year vs Budget", "es": "Actual (o prevision si falta) vs Last Year vs Budget"},
        "Proiezione (Actual/Previsione) vs Last Year/Budget": {"en": "Projection (Actual/Forecast) vs Last Year/Budget", "fr": "Projection (Actual/Prevision) vs Last Year/Budget", "es": "Proyeccion (Actual/Prevision) vs Last Year/Budget"},
        "Analisi KPI Settimanale": {"en": "Weekly KPI analysis", "fr": "Analyse KPI hebdomadaire", "es": "Analisis KPI semanal"},
        "Cruscotto - Analisi KPI Settimanale": {"en": "Control panel - Weekly KPI analysis", "fr": "Pilotage - Analyse KPI hebdomadaire", "es": "Panel de control - Analisis KPI semanal"},
        "Store:": {"en": "Store:", "fr": "Store :", "es": "Store:"},
        "Confronto revenues": {"en": "Revenue comparison", "fr": "Comparaison revenus", "es": "Comparacion ingresos"},
        "Week / Week-1 / MTD (con scontrini)": {"en": "Week / Week-1 / MTD (with receipts)", "fr": "Week / Week-1 / MTD (avec tickets)", "es": "Week / Week-1 / MTD (con tickets)"},
        "Rimborsi (aggregato)": {"en": "Refunds (aggregate)", "fr": "Remboursements (agrege)", "es": "Reembolsos (agregado)"},
        "Valore rimborsi vs annullati": {"en": "Refund value vs cancellations", "fr": "Valeur remboursements vs annulations", "es": "Valor reembolsos vs anulados"},
        "Costo del lavoro": {"en": "Labor cost", "fr": "Cout du travail", "es": "Coste laboral"},
        "Week - Week -1 - Progressivo mese": {"en": "Week - Week -1 - Month to date", "fr": "Week - Week -1 - Cumul mois", "es": "Week - Week -1 - Acumulado mes"},
        "Relazione settimanale": {"en": "Weekly report", "fr": "Rapport hebdomadaire", "es": "Informe semanal"},
        "Commenti e note": {"en": "Comments and notes", "fr": "Commentaires et notes", "es": "Comentarios y notas"},
        "Digita @ per inserire i KPI della pagina nella relazione.": {"en": "Type @ to insert this page's KPIs into the report.", "fr": "Tapez @ pour inserer les KPI de la page dans le rapport.", "es": "Escribe @ para insertar los KPI de la pagina en el informe."},
        "Ricarica": {"en": "Reload", "fr": "Recharger", "es": "Recargar"},
        "Scrivi qui la relazione della settimana...": {"en": "Write the weekly report here...", "fr": "Ecrivez ici le rapport de la semaine...", "es": "Escribe aqui el informe de la semana..."},
        "Overview": {"en": "Overview", "fr": "Vue d'ensemble", "es": "Resumen"},
        "Week detail": {"en": "Week detail", "fr": "Detail semaine", "es": "Detalle semana"},
        "Month detail": {"en": "Month detail", "fr": "Detail mois", "es": "Detalle mes"},
        "Previsione": {"en": "Forecast", "fr": "Prevision", "es": "Prevision"},
        "Prev.": {"en": "Prev.", "fr": "Prec.", "es": "Ant."},
        "Prev": {"en": "Prev", "fr": "Prec.", "es": "Ant."},
        "Proiezione": {"en": "Projection", "fr": "Projection", "es": "Proyeccion"},
        "Proj": {"en": "Proj", "fr": "Proj", "es": "Proy"},
        "parziale": {"en": "partial", "fr": "partiel", "es": "parcial"},
        "Last Year": {"en": "Last Year", "fr": "Annee precedente", "es": "Ano anterior"},
        "LY": {"en": "LY", "fr": "AP", "es": "AA"},
        "Stage": {"en": "Stage", "fr": "Stage", "es": "Practicas"},
        "Training": {"en": "Training", "fr": "Formation", "es": "Formacion"},
        "Produttivita": {"en": "Productivity", "fr": "Productivite", "es": "Productividad"},
        "Costo Lavoro": {"en": "Labor cost", "fr": "Cout du travail", "es": "Coste laboral"},
        "Ore": {"en": "Hours", "fr": "Heures", "es": "Horas"},
        "Ore totali": {"en": "Total hours", "fr": "Heures totales", "es": "Horas totales"},
        "Inc": {"en": "Inc", "fr": "Inc", "es": "Inc"},
        "Revenues (mese)": {"en": "Revenues (month)", "fr": "Revenus (mois)", "es": "Ingresos (mes)"},
        "Revenues (settimana)": {"en": "Revenues (week)", "fr": "Revenus (semaine)", "es": "Ingresos (semana)"},
        "Receipt (mese)": {"en": "Receipt (month)", "fr": "Tickets (mois)", "es": "Tickets (mes)"},
        "Receipt (settimana)": {"en": "Receipt (week)", "fr": "Tickets (semaine)", "es": "Tickets (semana)"},
        "Dati week": {"en": "Week data", "fr": "Donnees semaine", "es": "Datos semana"},
        "Scostamenti week": {"en": "Week variances", "fr": "Ecarts semaine", "es": "Desviaciones semana"},
        "Scostamenti week -1": {"en": "Week -1 variances", "fr": "Ecarts semaine -1", "es": "Desviaciones semana -1"},
        "Progressivo mese": {"en": "Month to date", "fr": "Cumul mois", "es": "Acumulado mes"},
        "Scostamenti": {"en": "Variances", "fr": "Ecarts", "es": "Desviaciones"},
        "Copertura dati settimana": {"en": "Week data coverage", "fr": "Couverture donnees semaine", "es": "Cobertura datos semana"},
        "Mancanti": {"en": "Missing", "fr": "Manquants", "es": "Faltantes"},
        "Totale delivery": {"en": "Total delivery", "fr": "Total delivery", "es": "Total delivery"},
        "Incidenza delivery": {"en": "Delivery incidence", "fr": "Incidence delivery", "es": "Incidencia delivery"},
        "Ordini delivery": {"en": "Delivery orders", "fr": "Commandes delivery", "es": "Pedidos delivery"},
        "% Apertura delivery stimata": {"en": "Estimated delivery opening %", "fr": "% ouverture delivery estimee", "es": "% apertura delivery estimada"},
        "Vendite perse stimate": {"en": "Estimated lost sales", "fr": "Ventes perdues estimees", "es": "Ventas perdidas estimadas"},
        "Ordini delivery su scontrini": {"en": "Delivery orders on receipts", "fr": "Commandes delivery sur tickets", "es": "Pedidos delivery sobre tickets"},
        "Scontrino medio": {"en": "Average receipt", "fr": "Ticket moyen", "es": "Ticket medio"},
        "Copertura calcolo": {"en": "Calculation coverage", "fr": "Couverture calcul", "es": "Cobertura calculo"},
        "Potenziale stimato": {"en": "Estimated potential", "fr": "Potentiel estime", "es": "Potencial estimado"},
        "Apertura/chiusura da configurazione provider": {"en": "Opening/closing from provider configuration", "fr": "Ouverture/fermeture depuis configuration provider", "es": "Apertura/cierre desde configuracion provider"},
        "Netto contestazioni": {"en": "Net of disputes", "fr": "Net contestations", "es": "Neto disputas"},
        "Valore rimborsi netto contestazioni accettate": {"en": "Refund value net of accepted disputes", "fr": "Valeur remboursements nette contestations acceptees", "es": "Valor reembolsos neto disputas aceptadas"},
        "Chiusura ribaltata": {"en": "Reversed closing", "fr": "Fermeture inversee", "es": "Cierre invertido"},
        "Apertura": {"en": "Opening", "fr": "Ouverture", "es": "Apertura"},
        "Perse stimate": {"en": "Estimated lost", "fr": "Pertes estimees", "es": "Perdidas estimadas"},
        "Potenziale": {"en": "Potential", "fr": "Potentiel", "es": "Potencial"},
        "Scontrino": {"en": "Receipt", "fr": "Ticket", "es": "Ticket"},
        "Valore rimborsi": {"en": "Refund value", "fr": "Valeur remboursements", "es": "Valor reembolsos"},
        "Rimborsi annullati": {"en": "Cancelled refunds", "fr": "Remboursements annules", "es": "Reembolsos cancelados"},
        "Rimborsi netti": {"en": "Net refunds", "fr": "Remboursements nets", "es": "Reembolsos netos"},
        "Incidenza valore rimborsi su totale delivery": {"en": "Refund value incidence on total delivery", "fr": "Incidence valeur remboursements sur total delivery", "es": "Incidencia valor reembolsos sobre total delivery"},
        "% apertura stimata": {"en": "Estimated opening %", "fr": "% ouverture estimee", "es": "% apertura estimada"},
        "copertura": {"en": "coverage", "fr": "couverture", "es": "cobertura"},
        "% su revenues": {"en": "% on revenues", "fr": "% sur revenues", "es": "% sobre revenues"},
        "MTD fino a week -1": {"en": "MTD up to week -1", "fr": "MTD jusqu'a week -1", "es": "MTD hasta week -1"},
        "Lun": {"en": "Mon", "fr": "Lun", "es": "Lun"},
        "Mar": {"en": "Tue", "fr": "Mar", "es": "Mar"},
        "Mer": {"en": "Wed", "fr": "Mer", "es": "Mie"},
        "Gio": {"en": "Thu", "fr": "Jeu", "es": "Jue"},
        "Ven": {"en": "Fri", "fr": "Ven", "es": "Vie"},
        "Sab": {"en": "Sat", "fr": "Sam", "es": "Sab"},
        "Dom": {"en": "Sun", "fr": "Dim", "es": "Dom"},
        "Nessuna relazione salvata per questa settimana.": {"en": "No report saved for this week.", "fr": "Aucun rapport enregistre pour cette semaine.", "es": "No hay informe guardado para esta semana."},
        "Ultimo salvataggio": {"en": "Last save", "fr": "Dernier enregistrement", "es": "Ultimo guardado"},
        "Errore caricamento relazione": {"en": "Report loading error", "fr": "Erreur chargement rapport", "es": "Error cargando informe"},
        "Relazione salvata.": {"en": "Report saved.", "fr": "Rapport enregistre.", "es": "Informe guardado."},
        "Errore salvataggio.": {"en": "Save error.", "fr": "Erreur enregistrement.", "es": "Error al guardar."},
        "Nessuna distinta salvata per questa data.": {"en": "No statement saved for this date.", "fr": "Aucune caisse enregistree pour cette date.", "es": "No hay caja guardada para esta fecha."},
        "Nessuna foto associata a questa giornata.": {"en": "No photo linked to this day.", "fr": "Aucune photo associee a cette journee.", "es": "Ninguna foto asociada a este dia."},
        "Seleziona prima uno store per usare la Distinta cassa.": {"en": "Select a store before using the cash statement.", "fr": "Selectionnez d'abord un store pour utiliser la caisse.", "es": "Selecciona primero una tienda para usar la caja."},
        "Distinta 1": {"en": "Deposit 1", "fr": "Remise 1", "es": "Entrega 1"},
        "Distinta 2": {"en": "Deposit 2", "fr": "Remise 2", "es": "Entrega 2"},
        "Giornata inclusa nel periodo competenza di un versamento: le distinte contanti non sono modificabili e la distinta non e eliminabile.": {"en": "This day is included in a deposit period: cash deposits cannot be edited and the statement cannot be deleted.", "fr": "Cette journee est incluse dans une periode de versement : les remises especes ne peuvent pas etre modifiees et la caisse ne peut pas etre supprimee.", "es": "Este dia esta incluido en un periodo de ingreso: las entregas de efectivo no se pueden modificar y la caja no se puede eliminar."},
        "Giornata convalidata: solo l'amministratore puo modificare o eliminare la distinta di cassa.": {"en": "Validated day: only the administrator can edit or delete the cash statement.", "fr": "Journee validee : seul l'administrateur peut modifier ou supprimer la caisse.", "es": "Dia validado: solo el administrador puede modificar o eliminar la caja."},
        "Giornata bloccata da versamento: non e possibile eliminare la distinta.": {"en": "Day locked by deposit: the statement cannot be deleted.", "fr": "Journee bloquee par un versement : la caisse ne peut pas etre supprimee.", "es": "Dia bloqueado por ingreso: no se puede eliminar la caja."},
        "Giornata convalidata: modifiche consentite solo all'amministratore.": {"en": "Validated day: changes are allowed only for the administrator.", "fr": "Journee validee : modifications autorisees uniquement pour l'administrateur.", "es": "Dia validado: solo el administrador puede hacer cambios."},
        "Eliminare la foto associata a questa giornata?": {"en": "Delete the photo linked to this day?", "fr": "Supprimer la photo associee a cette journee ?", "es": "Eliminar la foto asociada a este dia?"},
        "Apri foto": {"en": "Open photo", "fr": "Ouvrir photo", "es": "Abrir foto"},
        "Elimina foto": {"en": "Delete photo", "fr": "Supprimer photo", "es": "Eliminar foto"},
        "Chiudi": {"en": "Close", "fr": "Fermer", "es": "Cerrar"},
        "Caricamento...": {"en": "Loading...", "fr": "Chargement...", "es": "Cargando..."},
        "Foto chiusure": {"en": "Closing photo", "fr": "Photo cloture", "es": "Foto de cierre"},
        "Impossibile caricare la foto.": {"en": "Unable to load the photo.", "fr": "Impossible de charger la photo.", "es": "No se puede cargar la foto."},
        "Stai per eliminare tutti i dati della distinta del giorno": {"en": "You are about to delete all statement data for the day", "fr": "Vous allez supprimer toutes les donnees de caisse du jour", "es": "Estas a punto de eliminar todos los datos de caja del dia"},
        "Elimina anche la foto associata": {"en": "Also delete the linked photo", "fr": "Supprimer aussi la photo associee", "es": "Eliminar tambien la foto asociada"},
        "Operazione irreversibile.": {"en": "This action cannot be undone.", "fr": "Operation irreversible.", "es": "Operacion irreversible."},
        "Annulla": {"en": "Cancel", "fr": "Annuler", "es": "Cancelar"},
        "Elimina": {"en": "Delete", "fr": "Supprimer", "es": "Eliminar"},
        "Import iPratico completato con avvisi": {"en": "iPratico import completed with warnings", "fr": "Import iPratico termine avec avertissements", "es": "Importacion iPratico completada con avisos"},
        "altri avvisi": {"en": "more warnings", "fr": "autres avertissements", "es": "mas avisos"},
        "Errore import iPratico": {"en": "iPratico import error", "fr": "Erreur import iPratico", "es": "Error importacion iPratico"},
        "L'import iPratico sovrascrivera i campi chiusura importabili e sostituira Delivery/Coupon gia presenti. Continuare?": {"en": "The iPratico import will overwrite importable closing fields and replace existing Delivery/Coupon entries. Continue?", "fr": "L'import iPratico va ecraser les champs de cloture importables et remplacer les lignes Delivery/Coupon existantes. Continuer ?", "es": "La importacion iPratico sobrescribira los campos de cierre importables y reemplazara las lineas Delivery/Coupon existentes. Continuar?"},
        "Versamenti": {"en": "Deposits", "fr": "Versements", "es": "Ingresos"},
        "Gestione delivery": {"en": "Delivery management", "fr": "Gestion delivery", "es": "Gestion delivery"},
        "Ricerca": {"en": "Search", "fr": "Recherche", "es": "Busqueda"},
        "Spese di cassa": {"en": "Cash expenses", "fr": "Depenses de caisse", "es": "Gastos de caja"},
        "Seleziona uno store per inserire e visualizzare le spese.": {"en": "Select a store to enter and view expenses.", "fr": "Selectionnez un store pour saisir et afficher les depenses.", "es": "Selecciona una tienda para introducir y ver gastos."},
        "Mese": {"en": "Month", "fr": "Mois", "es": "Mes"},
        "Vai": {"en": "Go", "fr": "Aller", "es": "Ir"},
        "Nuova spesa": {"en": "New expense", "fr": "Nouvelle depense", "es": "Nuevo gasto"},
        "Tipo di operazione": {"en": "Operation type", "fr": "Type d'operation", "es": "Tipo de operacion"},
        "Scontrino": {"en": "Receipt", "fr": "Ticket", "es": "Ticket"},
        "Fattura": {"en": "Invoice", "fr": "Facture", "es": "Factura"},
        "Nota di credito": {"en": "Credit note", "fr": "Avoir", "es": "Nota de credito"},
        "Fornitore / Spesa": {"en": "Supplier / Expense", "fr": "Fournisseur / Depense", "es": "Proveedor / Gasto"},
        "Scontrino / Fattura": {"en": "Receipt / Invoice", "fr": "Ticket / Facture", "es": "Ticket / Factura"},
        "Importo": {"en": "Amount", "fr": "Montant", "es": "Importe"},
        "Foto fattura": {"en": "Invoice photo", "fr": "Photo facture", "es": "Foto factura"},
        "Salva spesa": {"en": "Save expense", "fr": "Enregistrer depense", "es": "Guardar gasto"},
        "Riepilogo mese": {"en": "Month summary", "fr": "Resume du mois", "es": "Resumen del mes"},
        "Azioni": {"en": "Actions", "fr": "Actions", "es": "Acciones"},
        "Vedi foto": {"en": "View photo", "fr": "Voir photo", "es": "Ver foto"},
        "Modifica": {"en": "Edit", "fr": "Modifier", "es": "Modificar"},
        "Eliminare questa spesa?": {"en": "Delete this expense?", "fr": "Supprimer cette depense ?", "es": "Eliminar este gasto?"},
        "Nessuna spesa trovata per il mese selezionato.": {"en": "No expenses found for the selected month.", "fr": "Aucune depense trouvee pour le mois selectionne.", "es": "No se encontraron gastos para el mes seleccionado."},
        "Modifica spesa": {"en": "Edit expense", "fr": "Modifier depense", "es": "Modificar gasto"},
        "Sostituisci foto (opzionale)": {"en": "Replace photo (optional)", "fr": "Remplacer la photo (optionnel)", "es": "Sustituir foto (opcional)"},
        "Se carichi un file, sostituisce la foto associata a questa spesa.": {"en": "If you upload a file, it replaces the photo linked to this expense.", "fr": "Si vous chargez un fichier, il remplace la photo associee a cette depense.", "es": "Si subes un archivo, sustituye la foto asociada a este gasto."},
        "Salva modifiche": {"en": "Save changes", "fr": "Enregistrer modifications", "es": "Guardar cambios"},
        "Foto spesa": {"en": "Expense photo", "fr": "Photo depense", "es": "Foto gasto"},
        "Taglio contanti": {"en": "Cash denomination", "fr": "Coupure especes", "es": "Denominacion efectivo"},
        "Voce ticket": {"en": "Meal voucher item", "fr": "Ligne ticket repas", "es": "Linea ticket restaurante"},
        "Voce delivery": {"en": "Delivery item", "fr": "Ligne delivery", "es": "Linea delivery"},
        "Voce coupon": {"en": "Coupon item", "fr": "Ligne coupon", "es": "Linea cupon"},
        "Seleziona uno store per inserire e visualizzare i versamenti.": {"en": "Select a store to enter and view deposits.", "fr": "Selectionnez un store pour saisir et afficher les versements.", "es": "Selecciona una tienda para introducir y ver ingresos."},
        "Nuovo versamento": {"en": "New deposit", "fr": "Nouveau versement", "es": "Nuevo ingreso"},
        "Data versamento": {"en": "Deposit date", "fr": "Date versement", "es": "Fecha ingreso"},
        "Periodo competenza - Dal": {"en": "Reference period - From", "fr": "Periode de reference - Du", "es": "Periodo de referencia - Desde"},
        "Periodo competenza - Al": {"en": "Reference period - To", "fr": "Periode de reference - Au", "es": "Periodo de referencia - Hasta"},
        "Nome e cognome": {"en": "Full name", "fr": "Nom et prenom", "es": "Nombre y apellidos"},
        "Tipo versamento": {"en": "Deposit type", "fr": "Type de versement", "es": "Tipo de ingreso"},
        "Operatore": {"en": "Operator", "fr": "Operateur", "es": "Operador"},
        "Tessera": {"en": "Card", "fr": "Carte", "es": "Tarjeta"},
        "Nome banca e distinta": {"en": "Bank name and slip", "fr": "Banque et bordereau", "es": "Banco y comprobante"},
        "Totale distinte periodo": {"en": "Period cash deposits total", "fr": "Total remises de la periode", "es": "Total entregas del periodo"},
        "Differenza": {"en": "Difference", "fr": "Ecart", "es": "Diferencia"},
        "Carica la foto della ricevuta di versamento, salvo una delle dichiarazioni sotto.": {"en": "Upload the deposit receipt photo, unless one of the declarations below applies.", "fr": "Chargez la photo du recu de versement, sauf si l'une des declarations ci-dessous s'applique.", "es": "Sube la foto del recibo de ingreso, salvo una de las declaraciones siguientes."},
        "Dichiaro che lo sportello non ha emesso la ricevuta": {"en": "I declare that the branch did not issue the receipt", "fr": "Je declare que le guichet n'a pas emis de recu", "es": "Declaro que la sucursal no emitio el recibo"},
        "Ricevuta smarrita": {"en": "Receipt lost", "fr": "Recu perdu", "es": "Recibo perdido"},
        "Salva versamento": {"en": "Save deposit", "fr": "Enregistrer versement", "es": "Guardar ingreso"},
        "Totale versamenti": {"en": "Total deposits", "fr": "Total versements", "es": "Total ingresos"},
        "Eliminare questo versamento?": {"en": "Delete this deposit?", "fr": "Supprimer ce versement ?", "es": "Eliminar este ingreso?"},
        "Nessun versamento trovato per il mese selezionato.": {"en": "No deposits found for the selected month.", "fr": "Aucun versement trouve pour le mois selectionne.", "es": "No se encontraron ingresos para el mes seleccionado."},
        "Modifica versamento": {"en": "Edit deposit", "fr": "Modifier versement", "es": "Modificar ingreso"},
        "Riferimento versamento": {"en": "Deposit reference", "fr": "Reference versement", "es": "Referencia ingreso"},
        "Se non c'e gia una foto associata, il caricamento e obbligatorio salvo una delle dichiarazioni sotto.": {"en": "If no photo is already linked, upload is required unless one of the declarations below applies.", "fr": "Si aucune photo n'est deja associee, le chargement est obligatoire sauf si l'une des declarations ci-dessous s'applique.", "es": "Si no hay una foto asociada, la carga es obligatoria salvo una de las declaraciones siguientes."},
        "Foto versamento": {"en": "Deposit photo", "fr": "Photo versement", "es": "Foto ingreso"},
        "Correzione distinte per chiudere la differenza": {"en": "Adjust cash deposits to close the difference", "fr": "Correction des remises pour solder l'ecart", "es": "Correccion de entregas para cerrar la diferencia"},
        "Bloccata": {"en": "Locked", "fr": "Bloquee", "es": "Bloqueada"},
        "Azione": {"en": "Action", "fr": "Action", "es": "Accion"},
        "Seleziona una o piu giornate da correggere: modifica i tagli/monete finche la differenza torna a zero.": {"en": "Select one or more days to adjust: edit denominations/coins until the difference returns to zero.", "fr": "Selectionnez une ou plusieurs journees a corriger : modifiez coupures/pieces jusqu'a ramener l'ecart a zero.", "es": "Selecciona uno o mas dias para corregir: modifica denominaciones/monedas hasta que la diferencia vuelva a cero."},
        "Giornata": {"en": "Day", "fr": "Journee", "es": "Dia"},
        "Totale giornata": {"en": "Day total", "fr": "Total journee", "es": "Total dia"},
        "Seleziona una giornata per modificare le distinte.": {"en": "Select a day to edit the cash deposits.", "fr": "Selectionnez une journee pour modifier les remises.", "es": "Selecciona un dia para modificar las entregas."},
        "Monete": {"en": "Coins", "fr": "Pieces", "es": "Monedas"},
        "Tot.": {"en": "Tot.", "fr": "Tot.", "es": "Tot."},
        "Salva giornata": {"en": "Save day", "fr": "Enregistrer journee", "es": "Guardar dia"},
        "Vendite": {"en": "Sales", "fr": "Ventes", "es": "Ventas"},
        "Importa distinta": {"en": "Import cash statement", "fr": "Importer caisse", "es": "Importar caja"},
        "I pagamenti vengono importati dalla distinta cassa della settimana selezionata ma restano modificabili prima del salvataggio.": {"en": "Payments are imported from the selected week's cash statement but remain editable before saving.", "fr": "Les paiements sont importes depuis la caisse de la semaine selectionnee mais restent modifiables avant enregistrement.", "es": "Los pagos se importan desde la caja de la semana seleccionada pero siguen editables antes de guardar."},
        "Pagamento online": {"en": "Online payment", "fr": "Paiement en ligne", "es": "Pago online"},
        "Pagamento contanti": {"en": "Cash payment", "fr": "Paiement especes", "es": "Pago efectivo"},
        "Numero ordini": {"en": "Order count", "fr": "Nombre commandes", "es": "Numero pedidos"},
        "Ordini cancellati": {"en": "Cancelled orders", "fr": "Commandes annulees", "es": "Pedidos cancelados"},
        "Rimborsi": {"en": "Refunds", "fr": "Remboursements", "es": "Reembolsos"},
        "Valore rimborsi": {"en": "Refund value", "fr": "Valeur remboursements", "es": "Valor reembolsos"},
        "Contestazioni dei rimborsi": {"en": "Refund disputes", "fr": "Contestations remboursements", "es": "Disputas de reembolsos"},
        "Contestazioni accettate": {"en": "Accepted disputes", "fr": "Contestations acceptees", "es": "Disputas aceptadas"},
        "Valore rimborsi annullati": {"en": "Cancelled refund value", "fr": "Valeur remboursements annules", "es": "Valor reembolsos anulados"},
        "% Rimborsi su ordini": {"en": "% refunds on orders", "fr": "% remboursements sur commandes", "es": "% reembolsos sobre pedidos"},
        "% Rimborsi su ordini (netto contestazioni)": {"en": "% refunds on orders (net of disputes)", "fr": "% remboursements sur commandes (net contestations)", "es": "% reembolsos sobre pedidos (neto disputas)"},
        "Apertura": {"en": "Opening", "fr": "Ouverture", "es": "Apertura"},
        "Vendite potenziali stimate": {"en": "Estimated potential sales", "fr": "Ventes potentielles estimees", "es": "Ventas potenciales estimadas"},
        "Vendite perse stimate": {"en": "Estimated lost sales", "fr": "Ventes perdues estimees", "es": "Ventas perdidas estimadas"},
        "Rating settimanale": {"en": "Weekly rating", "fr": "Rating hebdomadaire", "es": "Rating semanal"},
        "Confronto settimana precedente": {"en": "Previous week comparison", "fr": "Comparaison semaine precedente", "es": "Comparacion semana anterior"},
        "Seleziona uno store per inserire e visualizzare i dati.": {"en": "Select a store to enter and view data.", "fr": "Selectionnez un store pour saisir et afficher les donnees.", "es": "Selecciona una tienda para introducir y ver datos."},
        "Settimana": {"en": "Week", "fr": "Semaine", "es": "Semana"},
        "Seleziona uno store per procedere.": {"en": "Select a store to continue.", "fr": "Selectionnez un store pour continuer.", "es": "Selecciona una tienda para continuar."},
        "Nessun provider delivery attivo per questo tenant.": {"en": "No active delivery provider for this tenant.", "fr": "Aucun provider delivery actif pour ce tenant.", "es": "No hay proveedores delivery activos para este tenant."},
        "Importa pagamenti da distinta": {"en": "Import payments from statement", "fr": "Importer paiements de la caisse", "es": "Importar pagos desde caja"},
        "Salva dati delivery": {"en": "Save delivery data", "fr": "Enregistrer donnees delivery", "es": "Guardar datos delivery"},
        "Store visibili": {"en": "Visible stores", "fr": "Stores visibles", "es": "Tiendas visibles"},
        "Cosa cercare": {"en": "What to search", "fr": "Objet de recherche", "es": "Que buscar"},
        "Periodo": {"en": "Period", "fr": "Periode", "es": "Periodo"},
        "Data (nella settimana)": {"en": "Date (within week)", "fr": "Date (dans la semaine)", "es": "Fecha (en la semana)"},
        "Dal": {"en": "From", "fr": "Du", "es": "Desde"},
        "Al": {"en": "To", "fr": "Au", "es": "Hasta"},
        "Cerca": {"en": "Search", "fr": "Rechercher", "es": "Buscar"},
        "Estrai Excel (vista corrente)": {"en": "Export Excel (current view)", "fr": "Exporter Excel (vue courante)", "es": "Exportar Excel (vista actual)"},
        "Filtra per": {"en": "Filter by", "fr": "Filtrer par", "es": "Filtrar por"},
        "Tutti i campi": {"en": "All fields", "fr": "Tous les champs", "es": "Todos los campos"},
        "Scrivi per filtrare...": {"en": "Type to filter...", "fr": "Tapez pour filtrer...", "es": "Escribe para filtrar..."},
        "Reset": {"en": "Reset", "fr": "Reinitialiser", "es": "Restablecer"},
        "Suggerimento: scegli il campo dalla tendina e digita il valore per filtrare i risultati.": {"en": "Tip: choose the field from the list and type the value to filter results.", "fr": "Astuce : choisissez le champ dans la liste et saisissez la valeur pour filtrer les resultats.", "es": "Sugerencia: elige el campo de la lista y escribe el valor para filtrar los resultados."},
        "Errore UI": {"en": "UI error", "fr": "Erreur interface", "es": "Error UI"},
        "Righe": {"en": "Rows", "fr": "Lignes", "es": "Filas"},
        "su": {"en": "of", "fr": "sur", "es": "de"},
        "Nessun risultato.": {"en": "No results.", "fr": "Aucun resultat.", "es": "Sin resultados."},
        "store non disponibile": {"en": "store unavailable", "fr": "store indisponible", "es": "tienda no disponible"},
        "Seleziona un intervallo valido.": {"en": "Select a valid range.", "fr": "Selectionnez un intervalle valide.", "es": "Selecciona un intervalo valido."},
        "Errore ricerca": {"en": "Search error", "fr": "Erreur recherche", "es": "Error busqueda"},
        "Errore export": {"en": "Export error", "fr": "Erreur export", "es": "Error exportacion"},
        "Oggi": {"en": "Today", "fr": "Aujourd'hui", "es": "Hoy"},
        "Dal": {"en": "From", "fr": "Du", "es": "Desde"},
        "Al": {"en": "To", "fr": "Au", "es": "Hasta"},
        "Filtra": {"en": "Filter", "fr": "Filtrer", "es": "Filtrar"},
        "Cerca": {"en": "Search", "fr": "Rechercher", "es": "Buscar"},
        "Estrai Excel": {"en": "Export Excel", "fr": "Exporter Excel", "es": "Exportar Excel"},
        "Conferma": {"en": "Confirm", "fr": "Confirmer", "es": "Confirmar"},
        "Raccolta dati": {"en": "Data collection", "fr": "Collecte de donnees", "es": "Recogida de datos"},
        "Mese precedente": {"en": "Previous month", "fr": "Mois precedent", "es": "Mes anterior"},
        "Vendite lorde": {"en": "Gross sales", "fr": "Ventes brutes", "es": "Ventas brutas"},
        "POS": {"en": "POS", "fr": "TPE", "es": "TPV"},
        "Ticket": {"en": "Ticket", "fr": "Ticket", "es": "Ticket"},
        "Coupon": {"en": "Coupon", "fr": "Coupon", "es": "Cupon"},
        "Fatture": {"en": "Invoices", "fr": "Factures", "es": "Facturas"},
        "Numero fatture": {"en": "Invoice count", "fr": "Nombre factures", "es": "Numero facturas"},
        "Omaggi": {"en": "Free items", "fr": "Offerts", "es": "Invitaciones"},
        "Vendite IVA 4%": {"en": "Sales VAT 4%", "fr": "Ventes TVA 4%", "es": "Ventas IVA 4%"},
        "Vendite IVA 22%": {"en": "Sales VAT 22%", "fr": "Ventes TVA 22%", "es": "Ventas IVA 22%"},
        "Spese": {"en": "Expenses", "fr": "Depenses", "es": "Gastos"},
        "Mese successivo": {"en": "Next month", "fr": "Mois suivant", "es": "Mes siguiente"},
        "Seleziona modalita dashboard": {"en": "Select dashboard mode", "fr": "Selectionner le mode dashboard", "es": "Selecciona modo dashboard"},
        "Totale DDT": {"en": "Total delivery notes", "fr": "Total BL", "es": "Total albaranes"},
        "Trasferimenti IN": {"en": "Transfers IN", "fr": "Transferts IN", "es": "Transferencias IN"},
        "Trasferimenti OUT": {"en": "Transfers OUT", "fr": "Transferts OUT", "es": "Transferencias OUT"},
        "Giro affari": {"en": "Turnover", "fr": "Chiffre d'affaires", "es": "Volumen de negocio"},
        "Scontrini": {"en": "Receipts", "fr": "Tickets", "es": "Tickets"},
        "Tot distinte": {"en": "Total cash deposits", "fr": "Total remises", "es": "Total entregas"},
        "Annullati": {"en": "Cancellations", "fr": "Annulations", "es": "Anulados"},
        "Diff. cassa": {"en": "Cash diff.", "fr": "Ecart caisse", "es": "Dif. caja"},
        "Ultima data versata": {"en": "Last deposit date", "fr": "Derniere date versee", "es": "Ultima fecha ingresada"},
        "Giorni non versati": {"en": "Undeposited days", "fr": "Jours non verses", "es": "Dias no ingresados"},
        "Totale da versare": {"en": "Amount to deposit", "fr": "Total a verser", "es": "Total a ingresar"},
        "Convalida periodo": {"en": "Validate period", "fr": "Valider periode", "es": "Validar periodo"},
        "Periodi convalidati": {"en": "Validated periods", "fr": "Periodes validees", "es": "Periodos validados"},
        "Dettaglio": {"en": "Detail", "fr": "Detail", "es": "Detalle"},
        "Totale (filtrato)": {"en": "Total (filtered)", "fr": "Total (filtre)", "es": "Total (filtrado)"},
        "Filtro": {"en": "Filter", "fr": "Filtre", "es": "Filtro"},
        "Totale (tutto)": {"en": "Total (all)", "fr": "Total (tout)", "es": "Total (todo)"},
        "Fornitore": {"en": "Supplier", "fr": "Fournisseur", "es": "Proveedor"},
        "Riepilogo giornata": {"en": "Daily summary", "fr": "Resume journee", "es": "Resumen diario"},
        "ecco i dati della giornata:": {"en": "here are the day's figures:", "fr": "voici les donnees de la journee :", "es": "estos son los datos del dia:"},
        "Buon lavoro!": {"en": "Have a good shift!", "fr": "Bon travail !", "es": "Buen trabajo!"},
        "Attenzione versamenti": {"en": "Deposit alert", "fr": "Alerte versements", "es": "Aviso ingresos"},
        "Diff. cassa totale": {"en": "Total cash diff.", "fr": "Ecart caisse total", "es": "Dif. caja total"},
        "Richiesto da": {"en": "Requested by", "fr": "Demande par", "es": "Solicitado por"},
        "Ruolo": {"en": "Role", "fr": "Role", "es": "Rol"},
        "Creato il": {"en": "Created at", "fr": "Cree le", "es": "Creado el"},
        "Vuoi eliminare questa convalida? I giorni torneranno modificabili per user e supervisor.": {"en": "Delete this validation? The days will become editable again for users and supervisors.", "fr": "Supprimer cette validation ? Les jours redeviendront modifiables pour user et supervisor.", "es": "Eliminar esta validacion? Los dias volveran a ser editables para user y supervisor."},
        "Nessun periodo convalidato per questo store.": {"en": "No validated period for this store.", "fr": "Aucune periode validee pour ce store.", "es": "No hay periodos validados para esta tienda."},
        "Differenza di cassa": {"en": "Cash difference", "fr": "Ecart de caisse", "es": "Diferencia de caja"},
        "Somma distinte": {"en": "Cash deposits sum", "fr": "Somme des remises", "es": "Suma entregas"},
        "Delivery Online": {"en": "Delivery online", "fr": "Delivery en ligne", "es": "Delivery online"},
        "Delivery Contanti": {"en": "Delivery cash", "fr": "Delivery especes", "es": "Delivery efectivo"},
        "Totale spese (net)": {"en": "Total expenses (net)", "fr": "Total depenses (net)", "es": "Total gastos (neto)"},
        "Spese totali": {"en": "Total expenses", "fr": "Depenses totales", "es": "Gastos totales"},
        "Personalizzazioni rendiconto": {"en": "Cash report customizations", "fr": "Personnalisations caisse", "es": "Personalizaciones caja"},
        "Confronto su data LY": {"en": "Comparison on LY date", "fr": "Comparaison date N-1", "es": "Comparacion fecha LY"},
        "Personalizzazioni rendiconto attive": {"en": "Active cash report customizations", "fr": "Personnalisations caisse actives", "es": "Personalizaciones caja activas"},
        "Stai per convalidare il periodo selezionato. L'operazione e irreversibile e, dopo la conferma, le distinte di quei giorni non saranno piu modificabili da user e supervisor.": {"en": "You are about to validate the selected period. This action cannot be undone and, after confirmation, the statements for those days will no longer be editable by users and supervisors.", "fr": "Vous allez valider la periode selectionnee. L'operation est irreversible et, apres confirmation, les caisses de ces jours ne seront plus modifiables par user et supervisor.", "es": "Vas a validar el periodo seleccionado. La operacion es irreversible y, tras confirmar, las cajas de esos dias ya no seran editables por user y supervisor."},
        "Somma differenze di cassa": {"en": "Cash differences sum", "fr": "Somme des ecarts de caisse", "es": "Suma diferencias de caja"},
        "Giorno": {"en": "Day", "fr": "Jour", "es": "Dia"},
        "Conferma convalida": {"en": "Confirm validation", "fr": "Confirmer validation", "es": "Confirmar validacion"},
        "Errore": {"en": "Error", "fr": "Erreur", "es": "Error"},
        "Risposta non JSON (probabile redirect/login/store).": {"en": "Non-JSON response (likely redirect/login/store).", "fr": "Reponse non JSON (probable redirection/login/store).", "es": "Respuesta no JSON (probable redireccion/login/store)."},
        "Nessun giorno nel periodo selezionato.": {"en": "No days in the selected period.", "fr": "Aucun jour dans la periode selectionnee.", "es": "No hay dias en el periodo seleccionado."},
        "Seleziona un periodo valido.": {"en": "Select a valid period.", "fr": "Selectionnez une periode valide.", "es": "Selecciona un periodo valido."},
        "Errore caricamento anteprima.": {"en": "Preview loading error.", "fr": "Erreur chargement apercu.", "es": "Error cargando vista previa."},
        "Anteprima non disponibile.": {"en": "Preview unavailable.", "fr": "Apercu indisponible.", "es": "Vista previa no disponible."},
        "Errore convalida periodo.": {"en": "Period validation error.", "fr": "Erreur validation periode.", "es": "Error validacion periodo."},
        "Nessun dato per questo giorno.": {"en": "No data for this day.", "fr": "Aucune donnee pour ce jour.", "es": "No hay datos para este dia."},
        "Destinazione": {"en": "Destination", "fr": "Destination", "es": "Destino"},
        "SITE2 non indicato": {"en": "SITE2 not specified", "fr": "SITE2 non indique", "es": "SITE2 no indicado"},
        "Nessun dato chiusura per questo giorno.": {"en": "No closing data for this day.", "fr": "Aucune donnee de cloture pour ce jour.", "es": "No hay datos de cierre para este dia."},
        "Personalizzazione": {"en": "Customization", "fr": "Personnalisation", "es": "Personalizacion"},
        "Nessun link in questa categoria.": {"en": "No links in this category.", "fr": "Aucun lien dans cette categorie.", "es": "No hay enlaces en esta categoria."},
        "Nessun link configurato.": {"en": "No links configured.", "fr": "Aucun lien configure.", "es": "No hay enlaces configurados."},
        "Analisi": {"en": "Analysis", "fr": "Analyse", "es": "Analisis"},
        "Consumi": {"en": "Consumption", "fr": "Consommations", "es": "Consumos"},
        "Inserimento DDT": {"en": "Delivery note entry", "fr": "Saisie BL", "es": "Entrada albaranes"},
        "Spesa": {"en": "Expense", "fr": "Depense", "es": "Gasto"},
        "Modifica DDT": {"en": "Edit delivery notes", "fr": "Modifier BL", "es": "Modificar albaranes"},
        "Dati Inventario": {"en": "Inventory data", "fr": "Donnees inventaire", "es": "Datos inventario"},
        "Modifica Dati Inventario": {"en": "Edit inventory data", "fr": "Modifier donnees inventaire", "es": "Modificar datos inventario"},
        "Data movimentazione": {"en": "Movement date", "fr": "Date mouvement", "es": "Fecha movimiento"},
        "Tipo movimentazione": {"en": "Movement type", "fr": "Type mouvement", "es": "Tipo movimiento"},
        "Fornitori": {"en": "Suppliers", "fr": "Fournisseurs", "es": "Proveedores"},
        "Seleziona fornitori...": {"en": "Select suppliers...", "fr": "Selectionner fournisseurs...", "es": "Seleccionar proveedores..."},
        "Seleziona tutto": {"en": "Select all", "fr": "Tout selectionner", "es": "Seleccionar todo"},
        "Deseleziona tutto": {"en": "Deselect all", "fr": "Tout deselectionner", "es": "Deseleccionar todo"},
        "Seleziona uno o piu fornitori.": {"en": "Select one or more suppliers.", "fr": "Selectionnez un ou plusieurs fournisseurs.", "es": "Selecciona uno o mas proveedores."},
        "Seleziona uno o più fornitori.": {"en": "Select one or more suppliers.", "fr": "Selectionnez un ou plusieurs fournisseurs.", "es": "Selecciona uno o mas proveedores."},
        "Seleziona almeno un fornitore.": {"en": "Select at least one supplier.", "fr": "Selectionnez au moins un fournisseur.", "es": "Selecciona al menos un proveedor."},
        "fornitori selezionati": {"en": "suppliers selected", "fr": "fournisseurs selectionnes", "es": "proveedores seleccionados"},
        "Totale parziale inventario": {"en": "Partial inventory total", "fr": "Total partiel inventaire", "es": "Total parcial inventario"},
        "SITE2 (trasferimento)": {"en": "SITE2 (transfer)", "fr": "SITE2 (transfert)", "es": "SITE2 (transferencia)"},
        "Seleziona store": {"en": "Select store", "fr": "Selectionner store", "es": "Seleccionar store"},
        "Seleziona store...": {"en": "Select store...", "fr": "Selectionner store...", "es": "Seleccionar store..."},
        "Errore caricamento store": {"en": "Store loading error", "fr": "Erreur chargement store", "es": "Error cargando store"},
        "Errore caricamento": {"en": "Loading error", "fr": "Erreur chargement", "es": "Error de carga"},
        "Nessuna riga con quantita inserite.": {"en": "No rows with quantities entered.", "fr": "Aucune ligne avec quantites saisies.", "es": "No hay filas con cantidades introducidas."},
        "Nessuna riga con quantità inserite.": {"en": "No rows with quantities entered.", "fr": "Aucune ligne avec quantites saisies.", "es": "No hay filas con cantidades introducidas."},
        "Salvato.": {"en": "Saved.", "fr": "Enregistre.", "es": "Guardado."},
        "Righe inserite inventario": {"en": "Inventory rows inserted", "fr": "Lignes inventaire inserees", "es": "Filas inventario insertadas"},
        "Righe inserite TX": {"en": "TX rows inserted", "fr": "Lignes TX inserees", "es": "Filas TX insertadas"},
        "Righe ignorate": {"en": "Rows skipped", "fr": "Lignes ignorees", "es": "Filas omitidas"},
        "Nessuna riga da esportare.": {"en": "No rows to export.", "fr": "Aucune ligne a exporter.", "es": "No hay filas para exportar."},
        "Errore export PDF": {"en": "PDF export error", "fr": "Erreur export PDF", "es": "Error exportando PDF"},
        "Accumulato": {"en": "Accumulated", "fr": "Cumule", "es": "Acumulado"},
        "Elimina riga": {"en": "Delete row", "fr": "Supprimer ligne", "es": "Eliminar fila"},
        "Elimina": {"en": "Delete", "fr": "Supprimer", "es": "Eliminar"},
        "Note operative": {"en": "Operational notes", "fr": "Notes operationnelles", "es": "Notas operativas"},
        "Cambio intestazione movimento": {"en": "Change movement header", "fr": "Modifier en-tete mouvement", "es": "Cambiar cabecera movimiento"},
        "Aggiorna tutte le righe del movimento": {"en": "Updates all movement rows", "fr": "Met a jour toutes les lignes du mouvement", "es": "Actualiza todas las filas del movimiento"},
        "Nuova data": {"en": "New date", "fr": "Nouvelle date", "es": "Nueva fecha"},
        "Nuovo tipo": {"en": "New type", "fr": "Nouveau type", "es": "Nuevo tipo"},
        "Nuovo SITE2": {"en": "New SITE2", "fr": "Nouveau SITE2", "es": "Nuevo SITE2"},
        "Dati header mancanti: data, tipo e fornitore.": {"en": "Missing header data: date, type and supplier.", "fr": "Donnees d'en-tete manquantes : date, type et fournisseur.", "es": "Faltan datos de cabecera: fecha, tipo y proveedor."},
        "Per TXIN/TXOUT devi selezionare SITE2.": {"en": "For TXIN/TXOUT you must select SITE2.", "fr": "Pour TXIN/TXOUT vous devez selectionner SITE2.", "es": "Para TXIN/TXOUT debes seleccionar SITE2."},
        "Per TXIN/TXOUT devi selezionare SITE2 prima di salvare.": {"en": "For TXIN/TXOUT you must select SITE2 before saving.", "fr": "Pour TXIN/TXOUT vous devez selectionner SITE2 avant d'enregistrer.", "es": "Para TXIN/TXOUT debes seleccionar SITE2 antes de guardar."},
        "Descrizione o codice non disponibili: impossibile eliminare la riga.": {"en": "Description or code unavailable: the row cannot be deleted.", "fr": "Description ou code indisponible : impossible de supprimer la ligne.", "es": "Descripcion o codigo no disponibles: no se puede eliminar la fila."},
        "Compila fornitore, tipo e data prima di salvare.": {"en": "Fill supplier, type and date before saving.", "fr": "Renseignez fournisseur, type et date avant d'enregistrer.", "es": "Completa proveedor, tipo y fecha antes de guardar."},
        "Nessuna riga modificata: nulla da salvare.": {"en": "No rows changed: nothing to save.", "fr": "Aucune ligne modifiee : rien a enregistrer.", "es": "No hay filas modificadas: nada que guardar."},
        "Confermi il salvataggio delle modifiche?": {"en": "Confirm saving the changes?", "fr": "Confirmez-vous l'enregistrement des modifications ?", "es": "Confirmas guardar los cambios?"},
        "Righe modificate": {"en": "Changed rows", "fr": "Lignes modifiees", "es": "Filas modificadas"},
        "altre righe": {"en": "other rows", "fr": "autres lignes", "es": "otras filas"},
        "Seleziona la nuova data prima di applicare.": {"en": "Select the new date before applying.", "fr": "Selectionnez la nouvelle date avant d'appliquer.", "es": "Selecciona la nueva fecha antes de aplicar."},
        "Applico la modifica di intestazione a tutte le righe del movimento?": {"en": "Apply the header change to all movement rows?", "fr": "Appliquer la modification d'en-tete a toutes les lignes du mouvement ?", "es": "Aplicar el cambio de cabecera a todas las filas del movimiento?"},
        "Ordini": {"en": "Orders", "fr": "Commandes", "es": "Pedidos"},
        "Anagrafica": {"en": "Registry", "fr": "Anagrafique", "es": "Registro"},
        "Orari": {"en": "Schedules", "fr": "Horaires", "es": "Horarios"},
        "Analisi Settimanale": {"en": "Weekly analysis", "fr": "Analyse hebdomadaire", "es": "Analisis semanal"},
        "Analisi KPI Settimanale": {"en": "Weekly KPI analysis", "fr": "Analyse KPI hebdomadaire", "es": "Analisis KPI semanal"},
        "Analisi Mensile": {"en": "Monthly analysis", "fr": "Analyse mensuelle", "es": "Analisis mensual"},
        "Dati Delivery": {"en": "Delivery data", "fr": "Donnees delivery", "es": "Datos delivery"},
        "Dati Magazzino": {"en": "Warehouse data", "fr": "Donnees stock", "es": "Datos almacen"},
    }
    translated = known.get(s, {}).get(code)
    if translated:
        return translated
    if code == "pt":
        return _auto_translate_pt_common(s)
    return s


def _auto_translate_pt_common(source_text: str) -> str:
    s = str(source_text or "").strip()
    known_pt = {
        "Inventario": "Inventario",
        "Inventario iniziale": "Inventario inicial",
        "Inventario finale": "Inventario final",
        "Trasferimenti In": "Transferencias de entrada",
        "Trasferimenti Out": "Transferencias de saida",
        "Waste Crudo": "Desperdicio cru",
        "Waste crudo": "Desperdicio cru",
        "Waste (crudo)": "Desperdicio cru",
        "Consumo": "Consumo",
        "Consumo %": "Consumo %",
        "Waste %": "Desperdicio %",
        "Dashboard": "Dashboard",
        "Cruscotto": "Painel de controlo",
        "Magazzino": "Armazem",
        "Ordini al fornitore": "Encomendas a fornecedores",
        "Rendiconto": "Relatorio de caixa",
        "Gestione Orari": "Gestao de horarios",
        "Link": "Links",
        "Controlli di gestione": "Controlo de gestao",
        "Estrazioni": "Exportacoes",
        "Estrazioni HQ": "Exportacoes HQ",
        "Master": "Master",
        "Admin": "Admin",
        "Wizard nuovo tenant": "Assistente novo tenant",
        "Tenant": "Tenants",
        "Assegnazioni tenant": "Atribuicoes tenant",
        "Utenti master": "Utilizadores master",
        "Admin tenant": "Admin tenant",
        "Traduzioni": "Traducoes",
        "Configurazione orari": "Configuracao de horarios",
        "Configurazione distinta cassa": "Configuracao de caixa",
        "Configurazione iPratico": "Configuracao iPratico",
        "Configurazione delivery": "Configuracao delivery",
        "Import storico giornaliero": "Importacao historico diario",
        "Pagine test": "Paginas de teste",
        "Utenti": "Utilizadores",
        "Store": "Lojas",
        "Area manager": "Area managers",
        "Fornitori": "Fornecedores",
        "Anagrafica Listini Prezzi": "Registo de tabelas de precos",
        "Listini Prezzi": "Tabelas de precos",
        "Account": "Conta",
        "Lingua": "Idioma",
        "Logout": "Logout",
        "Attendere": "Aguarde",
        "Caricamento...": "A carregar...",
        "Salvataggio in corso...": "A guardar...",
        "Operazione in corso...": "Operacao em curso...",
        "Seleziona...": "Selecionar...",
        "Si": "Sim",
        "No": "Nao",
        "Crea": "Criar",
        "Salva": "Guardar",
        "Modifica": "Alterar",
        "Elimina": "Eliminar",
        "Attivo": "Ativo",
        "Azioni": "Acoes",
        "Chiave": "Chave",
        "Ordine": "Ordem",
        "Ord.": "Ord.",
        "Tutti": "Todos",
        "Nessuno": "Nenhum",
        "Reset": "Repor",
        "Totale": "Total",
        "Periodo": "Periodo",
        "Data": "Data",
        "Tipo": "Tipo",
        "Importo": "Valor",
        "Voce": "Item",
        "Valore": "Valor",
        "Numero": "Numero",
        "Percentuale": "Percentagem",
        "Settimana": "Semana",
        "Mese": "Mes",
        "Anno": "Ano",
        "Giorno": "Dia",
        "Oggi": "Hoje",
        "Applica": "Aplicar",
        "Filtra": "Filtrar",
        "Ricerca": "Pesquisa",
        "Cerca": "Procurar",
        "Dettaglio": "Detalhe",
        "Riepilogo": "Resumo",
        "Nessun dato trovato con i filtri selezionati.": "Nenhum dado encontrado com os filtros selecionados.",
        "Store attivo:": "Loja ativa:",
        "Nessuno store selezionato": "Nenhuma loja selecionada",
        "Cambia store": "Alterar loja",
        "Distinta cassa": "Mapa de caixa",
        "Dati chiusura": "Dados de fecho",
        "Foto distinta": "Foto do mapa de caixa",
        "Foto": "Foto",
        "Distinte": "Depositos de caixa",
        "Ticket": "Vales refeicao",
        "Delivery": "Delivery",
        "Coupon": "Cupoes",
        "VENDITE LORDE": "Vendas brutas",
        "ANNULLATI": "Cancelamentos",
        "SCONTRINI": "Taloes",
        "POS": "Pagamentos por cartao",
        "CONTANTI": "Dinheiro",
        "TICKET": "Vales refeicao",
        "FATTURE": "Faturas",
        "NUMERO FATTURE": "Numero de faturas",
        "OMAGGI": "Ofertas",
        "VENDITE IVA 4%": "Vendas IVA 4%",
        "VENDITE IVA 22%": "Vendas IVA 22%",
        "GIRO AFFARI": "Volume de negocios",
        "SPESE": "Despesas",
        "DIFFERENZA CASSA": "Diferenca de caixa",
        "Import iPratico": "Importar iPratico",
        "Dato importato": "Valor importado",
        "Spese": "Despesas",
        "Note credito": "Notas de credito",
        "Taglio": "Denominacao",
        "Qta": "Qtd",
        "Quantita": "Quantidade",
        "Fornitore": "Fornecedor",
        "Fornitori": "Fornecedores",
        "Descrizione": "Descricao",
        "Gruppo": "Grupo",
        "Prezzo": "Preco",
        "Unita": "Unidade",
        "Magazzino iniziale": "Inventario inicial",
        "Magazzino finale": "Inventario final",
        "Carichi": "Entradas",
        "Vendite": "Vendas",
        "Famiglia": "Familia",
        "Zona produzione": "Zona de producao",
        "Piatto": "Prato",
        "Materia prima": "Materia-prima",
        "Ricette": "Receitas",
        "Nuova ricetta": "Nova receita",
        "Modifica ricetta": "Alterar receita",
        "Salva ricetta": "Guardar receita",
        "Disattiva": "Desativar",
        "Riattiva": "Reativar",
        "Attiva": "Ativa",
        "Spenta": "Desativada",
        "Foglio decongelazione": "Folha de descongelacao",
        "Foglio della spesa": "Folha de compras",
        "Piano offerta": "Plano de oferta",
        "Porzioni": "Porcoes",
        "Pranzo": "Almoco",
        "Cena": "Jantar",
        "Dal": "De",
        "Al": "A",
        "Estrai Excel": "Exportar Excel",
        "Esporta Excel": "Exportar Excel",
        "Chiudi": "Fechar",
        "Annulla": "Cancelar",
        "Nome": "Nome",
        "Provider": "Provider",
        "Master - Configurazione delivery": "Master - Configuracao delivery",
        "Apri gestione delivery": "Abrir gestao delivery",
        "Logo file": "Ficheiro logo",
        "Logo": "Logo",
        "Rating": "Rating",
        "Apertura": "Abertura",
        "Chiusura": "Fecho",
        "Etichetta apertura": "Etiqueta abertura",
        "% Apertura": "% Abertura",
        "Apertura %": "Abertura %",
        "Dati Delivery": "Dados delivery",
        "Righe trovate": "Linhas encontradas",
        "Righe analizzate": "Linhas analisadas",
        "Pagamento Online": "Pagamento online",
        "Pagamento online": "Pagamento online",
        "Pagamento contanti": "Pagamento em dinheiro",
        "Rimborsi richiesti": "Reembolsos pedidos",
        "Rimborsi contestati": "Reembolsos contestados",
        "Contestazioni accettate": "Contestacoes aceites",
        "Contestazioni dei rimborsi": "Contestacoes dos reembolsos",
        "Composizione incassi": "Composicao dos recebimentos",
        "dei rimborsi": "dos reembolsos",
        "Funnel rimborsi": "Funil de reembolsos",
        "Richieste complessive": "Pedidos totais",
        "sulle richieste": "sobre os pedidos",
        "Cancellati": "Cancelados",
        "Rimborsi": "Reembolsos",
        "Contestati": "Contestados",
        "Accettati": "Aceites",
        "Rimb. annullati": "Reemb. cancelados",
        "Monete totali": "Total moedas",
        "ONLINE": "ONLINE",
        "Apri foto": "Abrir foto",
        "Foto chiusure": "Foto fechos",
        "Operazione irreversibile.": "Operacao irreversivel.",
        "Import iPratico completato con avvisi": "Importacao iPratico concluida com avisos",
        "altri avvisi": "outros avisos",
        "Gestione delivery": "Gestao delivery",
        "Vai": "Ir",
        "Tipo di operazione": "Tipo de operacao",
        "Scontrino": "Talao",
        "Fattura": "Fatura",
        "Nota di credito": "Nota de credito",
        "Scontrino / Fattura": "Talao / Fatura",
        "Foto fattura": "Foto fatura",
        "Vedi foto": "Ver foto",
        "Sostituisci foto (opzionale)": "Substituir foto (opcional)",
        "Se carichi un file, sostituisce la foto associata a questa spesa.": "Se carregar um ficheiro, substitui a foto associada a esta despesa.",
        "Foto spesa": "Foto despesa",
        "Data versamento": "Data deposito",
        "Periodo competenza - Dal": "Periodo competencia - De",
        "Periodo competenza - Al": "Periodo competencia - A",
        "Nome e cognome": "Nome e apelido",
        "Tipo versamento": "Tipo deposito",
        "Tessera": "Cartao",
        "Differenza": "Diferenca",
        "Dichiaro che lo sportello non ha emesso la ricevuta": "Declaro que o terminal nao emitiu o recibo",
        "Ricevuta smarrita": "Recibo perdido",
        "Rif.": "Ref.",
        "Periodo - Dal": "Periodo - De",
        "Periodo - Al": "Periodo - A",
        "Riferimento versamento": "Referencia deposito",
        "Foto versamento": "Foto deposito",
        "Correzione distinte per chiudere la differenza": "Correcao dos depositos para fechar a diferenca",
        "Bloccata": "Bloqueada",
        "Azione": "Acao",
        "Giornata": "Dia",
        "Monete": "Moedas",
        "Tot.": "Tot.",
        "Cosa cercare": "O que procurar",
        "Estrai Excel (vista corrente)": "Exportar Excel (vista atual)",
        "Filtra per": "Filtrar por",
        "Tutti i campi": "Todos os campos",
        "Scrivi per filtrare...": "Escreva para filtrar...",
        "Righe": "Linhas",
        "su": "de",
        "Consumi": "Consumos",
        "Inserimento DDT": "Introducao DDT",
        "Spesa": "Despesa",
        "Dati Inventario": "Dados inventario",
        "Tipo periodo": "Tipo periodo",
        "Intervallo": "Intervalo",
        "Revenues (net)": "Receitas (liquidas)",
        "Revenues (net) periodo": "Receitas (liquidas) periodo",
        "FoodPaper": "FoodPaper",
        "Operating": "Operating",
        "Listino": "Tabela de precos",
        "Tabella riepilogativa": "Tabela resumo",
        "Revenues (net) totali": "Receitas (liquidas) totais",
        "Mostra dettagli": "Mostrar detalhes",
        "Nascondi dettagli": "Ocultar detalhes",
        "Tot EUR": "Total EUR",
        "Data documento": "Data documento",
        "Data consegna": "Data entrega",
        "Qta car": "Qtd cx",
        "Qta int": "Qtd int",
        "Colli": "Volumes",
        "Pezzi": "Pecas",
        "Pezzi/Kg": "Pecas/Kg",
        "Sconto": "Desconto",
        "Sconto %": "Desconto %",
        "Unità": "Unidade",
        "Usa solo numeri (decimali con , o .).": "Use apenas numeros (decimais com , ou .).",
        "Riga": "Linha",
        "Confermi?": "Confirma?",
        "Data movimentazione": "Data movimento",
        "SITE2 (trasferimento)": "SITE2 (transferencia)",
        "Righe inserite inventario": "Linhas de inventario inseridas",
        "Righe inserite TX": "Linhas TX inseridas",
        "Righe ignorate": "Linhas ignoradas",
        "Conv (KG -> PZ)": "Conv (KG -> PC)",
        "Accumulato": "Acumulado",
        "Note operative": "Notas operacionais",
        "Per eliminare una riga usa x: la cancellazione e immediata in DB.": "Para eliminar uma linha use x: a eliminacao e imediata na BD.",
        "Cambio intestazione movimento": "Alteracao cabecalho movimento",
        "Aggiorna tutte le righe del movimento": "Atualiza todas as linhas do movimento",
        "Raccolta dati": "Recolha de dados",
        "Personalizzazioni rendiconto": "Personalizacoes do relatorio de caixa",
        "Ciao": "Ola",
        "ecco i dati della giornata:": "aqui estao os dados do dia:",
        "BUDGET": "BUDGET",
        "VENDITE ANNO PRECEDENTE": "VENDAS ANO ANTERIOR",
        "PREVISIONE": "PREVISAO",
        "Confronto su data LY": "Comparacao com data LY",
        "Personalizzazioni rendiconto attive": "Personalizacoes do relatorio de caixa ativas",
        "Buon lavoro!": "Bom trabalho!",
        "Anteprima non disponibile.": "Pre-visualizacao indisponivel.",
        "Destinazione": "Destino",
        "SITE2 non indicato": "SITE2 nao indicado",
        "Personalizzazione": "Personalizacao",
        "Anagrafica": "Registo",
        "Orari": "Horarios",
        "Anagrafica Staff": "Registo staff",
        "Nome e Cognome": "Nome e apelido",
        "Inquadramento": "Enquadramento",
        "Scheduling": "Scheduling",
        "Aggiungi": "Adicionar",
        "Stato": "Estado",
        "Legenda colori (Orari)": "Legenda de cores (Horarios)",
        "Nome legenda": "Nome legenda",
        "Scegli uno dei colori disponibili nella pagina Orari.": "Escolha uma das cores disponiveis na pagina Horarios.",
        "Gestione Orari - Orari": "Gestao de horarios - Horarios",
        "Persone": "Pessoas",
        "Cerca...": "Procurar...",
        "Mostra lineare": "Mostrar linear",
        "PDF": "PDF",
        "Produttivita": "Produtividade",
        "Legenda colori:": "Legenda de cores:",
        "Attenzione": "Atencao",
        "Importa": "Importar",
        "Copia giornata": "Copiar dia",
        "Incolla": "Colar",
        "Svuota copia": "Limpar copia",
        "Lineare": "Linear",
        "Previsioni": "Previsoes",
        "Allineato a": "Alinhado a",
        "Nominativo": "Nome",
        "Campo obbligatorio": "Campo obrigatorio",
        "Causale": "Causal",
        "LUN": "SEG",
        "MAR": "TER",
        "MER": "QUA",
        "GIO": "QUI",
        "VEN": "SEX",
        "SAB": "SAB",
        "DOM": "DOM",
        "Lun": "Seg",
        "Mar": "Ter",
        "Mer": "Qua",
        "Gio": "Qui",
        "Ven": "Sex",
        "Sab": "Sab",
        "Dom": "Dom",
        "Panoramica": "Visao geral",
        "Grafici": "Graficos",
        "Revenues": "Receitas",
        "Receipt": "Taloes",
        "Average Receipt": "Talao medio",
        "Actual vs Last Year": "Actual vs ano anterior",
        "Overview": "Visao geral",
        "Week detail": "Detalhe semana",
        "Month detail": "Detalhe mes",
        "Actual": "Actual",
        "Prev.": "Prev.",
        "Proiezione": "Projecao",
        "Proj": "Proj",
        "Last Year": "Ano anterior",
        "LY": "LY",
        "Stage": "Stage",
        "Training": "Formacao",
        "Costo Lavoro": "Custo laboral",
        "Inc": "Inc",
        "Confronto revenues": "Comparacao receitas",
        "Rimborsi (aggregato)": "Reembolsos (agregado)",
        "Costo del lavoro": "Custo laboral",
        "Commenti e note": "Comentarios e notas",
        "Digita @ per inserire i KPI della pagina nella relazione.": "Digite @ para inserir os KPI da pagina no relatorio.",
        "Week": "Semana",
        "Week -1": "Semana -1",
        "MTD": "MTD",
        "Dati week": "Dados semana",
        "Scostamenti week": "Desvios semana",
        "Scostamenti week -1": "Desvios semana -1",
        "Scostamenti": "Desvios",
        "vs Budget": "vs Budget",
        "vs Last Year": "vs Ano anterior",
        "Mancanti": "Em falta",
        "gg": "dias",
        "Incidenza delivery": "Incidencia delivery",
        "% Apertura delivery stimata": "% abertura delivery estimada",
        "Scontrino medio": "Talao medio",
        "Copertura calcolo": "Cobertura calculo",
        "Potenziale stimato": "Potencial estimado",
        "Apertura/chiusura da configurazione provider": "Abertura/fecho da configuracao provider",
        "Netto contestazioni": "Liquido contestacoes",
        "Incidenza": "Incidencia",
        "Chiusura ribaltata": "Fecho invertido",
        "Perse stimate": "Perdas estimadas",
        "Potenziale": "Potencial",
        "Rimborsi annullati": "Reembolsos cancelados",
        "Rimborsi netti": "Reembolsos liquidos",
        "% apertura stimata": "% abertura estimada",
        "copertura": "cobertura",
        "% su revenues": "% sobre receitas",
        "MTD fino a week -1": "MTD ate semana -1",
        "Pagamento online": "Pagamento online",
        "Per i trasferimenti (TXIN/TXOUT) puoi anche correggere mittente/destinatario cambiando tipo e/o SITE2.": "Para transferencias (TXIN/TXOUT) tambem pode corrigir origem/destino alterando tipo e/ou SITE2.",
        "Totpz (attuale)": "Totpz (atual)",
        "Tot EUR (attuale)": "Tot EUR (atual)",
        "altre righe": "outras linhas",
        "Cambio date per tutto il DDT": "Alteracao de datas para todo o DDT",
        "Applica cambio date": "Aplicar alteracao de datas",
        "Qta (calcolata)": "Qtd (calculada)",
        "Dati mancanti per eliminare la riga.": "Dados em falta para eliminar a linha.",
        "Confermi eliminazione della riga": "Confirma a eliminacao da linha",
        "Confermi cambio date su tutte le righe del DDT?": "Confirma a alteracao das datas em todas as linhas do DDT?",
        "Da": "De",
        "A": "A",
        "Tipo movimentazione": "Tipo movimento",
        "Tutte": "Todas",
        "Trasferimento IN": "Transferencia IN",
        "Trasferimento OUT": "Transferencia OUT",
        "Dettagli": "Detalhes",
        "Apri": "Abrir",
        "Intervallo non valido: la data di fine e precedente alla data di inizio.": "Intervalo invalido: a data final e anterior a data inicial.",
        "Trasferimenti IN": "Transferencias IN",
        "Trasferimenti OUT": "Transferencias OUT",
        "Waste": "Desperdicio",
        "Giro affari": "Volume de negocios",
        "Tot distinte": "Total depositos",
        "Annullati": "Cancelados",
        "Ultima data versata": "Ultima data depositada",
        "Giorni non versati": "Dias nao depositados",
        "Omaggi": "Ofertas",
        "Convalida periodo": "Validar periodo",
        "Periodi convalidati": "Periodos validados",
        "Richiesto da": "Pedido por",
        "Ruolo": "Funcao",
        "Filtro": "Filtro",
        "Somma distinte": "Soma depositos",
        "Delivery Online": "Delivery Online",
    }
    if s in known_pt:
        return known_pt[s]
    replacements = [
        ("Nessuna", "Nenhuma"),
        ("Nessun", "Nenhum"),
        ("nessuna", "nenhuma"),
        ("nessun", "nenhum"),
        ("Nuovo", "Novo"),
        ("Nuova", "Nova"),
        ("nuovo", "novo"),
        ("nuova", "nova"),
        ("Modifica", "Alterar"),
        ("modifica", "alteracao"),
        ("Elimina", "Eliminar"),
        ("elimina", "elimina"),
        ("Salva", "Guardar"),
        ("salva", "guarda"),
        ("Crea", "Criar"),
        ("crea", "cria"),
        ("Carica", "Carregar"),
        ("carica", "carrega"),
        ("Scarica", "Transferir"),
        ("scarica", "transfere"),
        ("Seleziona", "Selecionar"),
        ("seleziona", "seleciona"),
        ("Compila", "Preencher"),
        ("compila", "preenche"),
        ("Inserisci", "Introduzir"),
        ("inserisci", "introduz"),
        ("Visualizza", "Ver"),
        ("visualizza", "ver"),
        ("Ripristina", "Repor"),
        ("ripristina", "repoe"),
        ("Attiva", "Ativar"),
        ("attiva", "ativa"),
        ("Disattiva", "Desativar"),
        ("disattiva", "desativa"),
        ("Caricamento", "Carregamento"),
        ("Salvataggio", "Guardar"),
        ("Errore", "Erro"),
        ("errore", "erro"),
        ("Avviso", "Aviso"),
        ("avviso", "aviso"),
        ("Conferma", "Confirmacao"),
        ("conferma", "confirma"),
        ("Riepilogo", "Resumo"),
        ("riepilogo", "resumo"),
        ("Dettaglio", "Detalhe"),
        ("dettaglio", "detalhe"),
        ("Totale", "Total"),
        ("totale", "total"),
        ("Parziale", "Parcial"),
        ("parziale", "parcial"),
        ("Periodo", "Periodo"),
        ("periodo", "periodo"),
        ("Settimana", "Semana"),
        ("settimana", "semana"),
        ("Mese", "Mes"),
        ("mese", "mes"),
        ("Anno", "Ano"),
        ("anno", "ano"),
        ("Giorno", "Dia"),
        ("giorno", "dia"),
        ("Data", "Data"),
        ("data", "data"),
        ("Store", "Loja"),
        ("store", "loja"),
        ("Utente", "Utilizador"),
        ("utente", "utilizador"),
        ("Utenti", "Utilizadores"),
        ("utenti", "utilizadores"),
        ("Fornitore", "Fornecedor"),
        ("fornitore", "fornecedor"),
        ("Fornitori", "Fornecedores"),
        ("fornitori", "fornecedores"),
        ("Prodotto", "Produto"),
        ("prodotto", "produto"),
        ("Prodotti", "Produtos"),
        ("prodotti", "produtos"),
        ("Articolo", "Artigo"),
        ("articolo", "artigo"),
        ("Articoli", "Artigos"),
        ("articoli", "artigos"),
        ("Descrizione", "Descricao"),
        ("descrizione", "descricao"),
        ("Quantita", "Quantidade"),
        ("quantita", "quantidade"),
        ("Prezzo", "Preco"),
        ("prezzo", "preco"),
        ("Valore", "Valor"),
        ("valore", "valor"),
        ("Importo", "Valor"),
        ("importo", "valor"),
        ("Codice", "Codigo"),
        ("codice", "codigo"),
        ("Gruppo", "Grupo"),
        ("gruppo", "grupo"),
        ("Tipo", "Tipo"),
        ("tipo", "tipo"),
        ("Voce", "Item"),
        ("voce", "item"),
        ("Voci", "Itens"),
        ("voci", "itens"),
        ("Cassa", "Caixa"),
        ("cassa", "caixa"),
        ("Distinta", "Mapa"),
        ("distinta", "mapa"),
        ("Spese", "Despesas"),
        ("spese", "despesas"),
        ("Versamenti", "Depositos"),
        ("versamenti", "depositos"),
        ("Contanti", "Dinheiro"),
        ("contanti", "dinheiro"),
        ("Online", "Online"),
        ("Ticket", "Vales refeicao"),
        ("Coupon", "Cupoes"),
        ("Ordini", "Encomendas"),
        ("ordini", "encomendas"),
        ("Scontrini", "Taloes"),
        ("scontrini", "taloes"),
        ("Vendite", "Vendas"),
        ("vendite", "vendas"),
        ("Fatture", "Faturas"),
        ("fatture", "faturas"),
        ("Magazzino", "Armazem"),
        ("magazzino", "armazem"),
        ("Inventario", "Inventario"),
        ("Analisi", "Analise"),
        ("analisi", "analise"),
        ("Previsione", "Previsao"),
        ("previsione", "previsao"),
        ("Budget", "Budget"),
        ("Ore", "Horas"),
        ("ore", "horas"),
        ("Costo lavoro", "Custo laboral"),
        ("costo lavoro", "custo laboral"),
        ("Ricerca", "Pesquisa"),
        ("ricerca", "pesquisa"),
        ("Visibile", "Visivel"),
        ("visibile", "visivel"),
        ("Attivo", "Ativo"),
        ("attivo", "ativo"),
        ("Default", "Padrao"),
        ("Lingua", "Idioma"),
        ("Traduzioni", "Traducoes"),
    ]
    out = s
    for src, dst in replacements:
        out = out.replace(src, dst)
    return out


def _upsert_translation(conn, namespace: str, translation_key: str, language_code: str, source_text: str, text_value: str, *, auto: bool, customized: bool) -> None:
    cur = conn.cursor()
    cur.execute(
        """
SELECT COUNT(1)
FROM dbo.StoreHubTranslations
WHERE namespace = ? AND translation_key = ? AND language_code = ?
""",
        (namespace, translation_key, language_code),
    )
    exists = int((cur.fetchone() or [0])[0] or 0) > 0
    if exists:
        cur.execute(
            """
UPDATE dbo.StoreHubTranslations
SET source_text = ?, text_value = ?, auto_translated = ?, customized = ?, updated_at = SYSUTCDATETIME()
WHERE namespace = ? AND translation_key = ? AND language_code = ?
""",
            (
                source_text,
                text_value,
                1 if auto else 0,
                1 if customized else 0,
                namespace,
                translation_key,
                language_code,
            ),
        )
        return
    cur.execute(
        """
INSERT INTO dbo.StoreHubTranslations
  (namespace, translation_key, language_code, source_text, text_value, auto_translated, customized)
VALUES (?, ?, ?, ?, ?, ?, ?)
""",
        (
            namespace,
            translation_key,
            language_code,
            source_text,
            text_value,
            1 if auto else 0,
            1 if customized else 0,
        ),
    )


def _backfill_supported_languages_on_connection(conn) -> int:
    _ensure_schema_on_connection(conn)
    cur = conn.cursor()
    cur.execute(
        """
SELECT namespace, translation_key,
       MAX(CASE WHEN language_code = 'it' THEN source_text ELSE '' END) AS it_source,
       MAX(CASE WHEN language_code = 'it' THEN text_value ELSE '' END) AS it_value,
       MAX(source_text) AS any_source,
       MAX(CASE WHEN COALESCE(customized, 0) = 1 THEN 1 ELSE 0 END) AS has_custom
FROM dbo.StoreHubTranslations
GROUP BY namespace, translation_key
"""
    )
    keys = cur.fetchall() or []
    changed = 0
    for r in keys:
        ns = str(r[0] or "").strip()
        key = str(r[1] or "").strip()
        source = str(r[2] or r[3] or r[4] or "").strip()
        customized = bool(r[5])
        if not ns or not key or not source:
            continue
        for lang in SUPPORTED_LANGUAGES:
            code = str(lang.get("code") or "").strip().lower()
            cur.execute(
                """
SELECT COUNT(1)
FROM dbo.StoreHubTranslations
WHERE namespace = ? AND translation_key = ? AND language_code = ?
""",
                (ns, key, code),
            )
            exists = int((cur.fetchone() or [0])[0] or 0) > 0
            if exists:
                if code != "it":
                    refreshed_value = _auto_translate(source, code)
                    cur.execute(
                        """
UPDATE dbo.StoreHubTranslations
SET source_text = ?, text_value = ?, auto_translated = 1, updated_at = SYSUTCDATETIME()
WHERE namespace = ?
  AND translation_key = ?
  AND language_code = ?
  AND COALESCE(auto_translated, 1) = 1
""",
                        (source, refreshed_value, ns, key, code),
                    )
                    changed += int(cur.rowcount or 0)
                continue
            value = source if code == "it" else _auto_translate(source, code)
            _upsert_translation(conn, ns, key, code, source, value, auto=(code != "it"), customized=customized)
            changed += 1
    if changed:
        conn.commit()
    return changed


def backfill_supported_languages(*, include_platform: bool = True, include_tenant: bool = True) -> int:
    total = 0
    if include_platform:
        conn = _connect_platform()
        try:
            total += _backfill_supported_languages_on_connection(conn)
        finally:
            try:
                conn.close()
            except Exception:
                pass
    if include_tenant:
        try:
            conn = get_connection(None)
            try:
                total += _backfill_supported_languages_on_connection(conn)
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
        except Exception:
            pass
    return total


def seed_translation_key(namespace: str, translation_key: str, source_text: str) -> None:
    ensure_platform_translations_schema()
    ns = str(namespace or "common").strip()
    key = str(translation_key or "").strip()
    source = str(source_text or "").strip()
    if not ns or not key or not source:
        return

    conn = _connect_platform()
    try:
        cur = conn.cursor()
        for lang in SUPPORTED_LANGUAGES:
            code = lang["code"]
            value = _auto_translate(source, code)
            cur.execute(
                """
SELECT COUNT(1)
FROM dbo.StoreHubTranslations
WHERE namespace = ? AND translation_key = ? AND language_code = ?
""",
                (ns, key, code),
            )
            exists = int((cur.fetchone() or [0])[0] or 0) > 0
            if exists:
                cur.execute(
                    """
UPDATE dbo.StoreHubTranslations
SET source_text = ?, text_value = ?, auto_translated = 1, updated_at = SYSUTCDATETIME()
WHERE namespace = ?
  AND translation_key = ?
  AND language_code = ?
  AND COALESCE(customized, 0) = 0
  AND COALESCE(auto_translated, 1) = 1
""",
                    (source, value, ns, key, code),
                )
                continue
            cur.execute(
                """
INSERT INTO dbo.StoreHubTranslations
  (namespace, translation_key, language_code, source_text, text_value, auto_translated, customized)
VALUES (?, ?, ?, ?, ?, ?, 0)
""",
                (ns, key, code, source, value, 1 if code != "it" else 0),
            )
        conn.commit()
        invalidate_translation_cache()
    finally:
        try:
            conn.close()
        except Exception:
            pass


def seed_base_translations() -> None:
    rows = [
        ("navigation", "nav.dashboard", "Dashboard"),
        ("navigation", "nav.cruscotto", "Cruscotto"),
        ("navigation", "nav.magazzino", "Magazzino"),
        ("navigation", "nav.supplier_orders", "Ordini al fornitore"),
        ("navigation", "nav.mbo", "MBO"),
        ("navigation", "nav.rendiconto", "Rendiconto"),
        ("navigation", "nav.orari", "Gestione Orari"),
        ("navigation", "nav.links", "Link"),
        ("navigation", "nav.controlli", "Controlli di gestione"),
        ("navigation", "nav.estrazioni", "Estrazioni"),
        ("navigation", "nav.estrazioni_hq", "Estrazioni HQ"),
        ("navigation", "nav.master", "Master"),
        ("navigation", "nav.admin", "Admin"),
        ("navigation", "master.wizard", "Wizard nuovo tenant"),
        ("navigation", "master.tenants", "Tenant"),
        ("navigation", "master.assignments", "Assegnazioni tenant"),
        ("navigation", "master.users", "Utenti master"),
        ("navigation", "master.admin_tenant", "Admin tenant"),
        ("navigation", "master.translations", "Traduzioni"),
        ("navigation", "master.orari_config", "Configurazione orari"),
        ("navigation", "master.cash_config", "Configurazione distinta cassa"),
        ("navigation", "master.ipratico_config", "Configurazione iPratico"),
        ("navigation", "master.delivery_config", "Configurazione delivery"),
        ("navigation", "master.historical_import", "Import storico giornaliero"),
        ("navigation", "master.test_pages", "Pagine test"),
        ("navigation", "admin.users", "Utenti"),
        ("navigation", "admin.stores", "Store"),
        ("navigation", "admin.area_managers", "Area manager"),
        ("navigation", "admin.suppliers", "Fornitori"),
        ("navigation", "admin.price_registry", "Anagrafica Listini Prezzi"),
        ("navigation", "admin.price_lists", "Listini Prezzi"),
        ("common", "account", "Account"),
        ("common", "language", "Lingua"),
        ("common", "logout", "Logout"),
        ("common", "please_wait", "Attendere"),
        ("common", "loading", "Caricamento..."),
        ("common", "saving", "Salvataggio in corso..."),
        ("common", "operation_in_progress", "Operazione in corso..."),
        ("common", "active_store", "Store attivo:"),
        ("common", "no_store_selected", "Nessuno store selezionato"),
        ("common", "change_store", "Cambia store"),
        ("common", "today", "Oggi"),
        ("common", "from", "Dal"),
        ("common", "to", "Al"),
        ("common", "filter", "Filtra"),
        ("common", "search", "Cerca"),
        ("common", "export_excel", "Estrai Excel"),
        ("common", "close", "Chiudi"),
        ("common", "cancel", "Annulla"),
        ("common", "confirm", "Conferma"),
        ("common", "delete", "Elimina"),
        ("common", "save", "Salva"),
        ("common", "name", "Nome"),
        ("common", "select", "Seleziona..."),
        ("common", "yes", "Si"),
        ("common", "no", "No"),
        ("common", "create", "Crea"),
        ("common", "active", "Attivo"),
        ("common", "actions", "Azioni"),
        ("common", "key", "Chiave"),
        ("common", "sort_order", "Ordine"),
        ("common", "sort_order_short", "Ord."),
        ("common", "all", "Tutti"),
        ("common", "none", "Nessuno"),
        ("common", "reset", "Reset"),
        ("common", "total", "Totale"),
        ("common", "period", "Periodo"),
        ("common", "store", "Store"),
        ("common", "provider", "Provider"),
        ("master", "delivery_config.page_title", "Master - Configurazione delivery"),
        ("master", "delivery_config.title", "Configurazione delivery"),
        ("master", "delivery_config.help_statement_link", "Provider mostrati nella pagina Rendiconto - Gestione delivery. Il provider tecnico si seleziona dalle voci Delivery della distinta cassa."),
        ("master", "delivery_config.help_delete_scope", "L'eliminazione rimuove solo la configurazione provider del tenant selezionato."),
        ("master", "delivery_config.open_delivery_management", "Apri gestione delivery"),
        ("master", "delivery_config.new_provider", "Nuovo provider"),
        ("master", "delivery_config.statement_provider", "Provider distinta"),
        ("master", "delivery_config.logo_file", "Logo file"),
        ("master", "delivery_config.logo", "Logo"),
        ("master", "delivery_config.rating", "Rating"),
        ("master", "delivery_config.rating_number", "Numero"),
        ("master", "delivery_config.rating_percent", "Percentuale"),
        ("master", "delivery_config.opening", "Apertura"),
        ("master", "delivery_config.closure", "Chiusura"),
        ("master", "delivery_config.opening_label", "Etichetta apertura"),
        ("master", "delivery_config.opening_pct_default", "% Apertura"),
        ("master", "delivery_config.not_in_statement", "(non presente in distinta)"),
        ("master", "delivery_config.delete_confirm", "Eliminare questo provider delivery dalla configurazione? I dati settimanali gia salvati non verranno cancellati."),
        ("master", "delivery_config.no_provider", "Nessun provider configurato."),
        ("estrazioni", "delivery_data.title", "Dati Delivery"),
        ("estrazioni", "delivery_data.visible_stores", "Store visibili"),
        ("estrazioni", "delivery_data.week_from", "Settimana da"),
        ("estrazioni", "delivery_data.week_to", "Settimana a"),
        ("estrazioni", "delivery_data.selected_stores", "Store selezionati"),
        ("estrazioni", "delivery_data.rows_found", "Righe trovate"),
        ("estrazioni", "delivery_data.analysis_dashboard", "Cruscotto di analisi delivery"),
        ("estrazioni", "delivery_data.analysis_help", "Il riepilogo lavora sugli stessi dati filtrati nella tabella: incassi, rimborsi, recuperato e qualita del processo."),
        ("estrazioni", "delivery_data.analyzed_rows", "Righe analizzate"),
        ("estrazioni", "delivery_data.payment_online", "Pagamento Online"),
        ("estrazioni", "delivery_data.payment_cash", "Pagamento contanti"),
        ("estrazioni", "delivery_data.refunds_requested", "Rimborsi richiesti"),
        ("estrazioni", "delivery_data.refund_value", "Valore rimborsi"),
        ("estrazioni", "delivery_data.refunds_disputed", "Rimborsi contestati"),
        ("estrazioni", "delivery_data.accepted_disputes", "Contestazioni accettate"),
        ("estrazioni", "delivery_data.recovered_value", "Valore recuperato"),
        ("estrazioni", "delivery_data.completed_stores", "Negozi compilati"),
        ("estrazioni", "delivery_data.completed_stores_lower", "negozi compilati"),
        ("estrazioni", "delivery_data.payment_composition", "Composizione incassi"),
        ("estrazioni", "delivery_data.total_collected", "Totale incassato"),
        ("estrazioni", "delivery_data.of_refunds", "dei rimborsi"),
        ("estrazioni", "delivery_data.percentages_note", "Le percentuali sono calcolate sul totale dei dati filtrati e visualizzati nella tabella."),
        ("estrazioni", "delivery_data.refund_funnel", "Funnel rimborsi"),
        ("estrazioni", "delivery_data.total_requests", "Richieste complessive"),
        ("estrazioni", "delivery_data.on_requests", "sulle richieste"),
        ("estrazioni", "delivery_data.refund_percentages_note", "La percentuale contestazioni accettate e calcolata su rimborsi richiesti. La percentuale valore recuperato e calcolata sul valore rimborsi."),
        ("estrazioni", "delivery_data.week", "Settimana"),
        ("estrazioni", "delivery_data.cash", "Contanti"),
        ("estrazioni", "delivery_data.online", "Online"),
        ("estrazioni", "delivery_data.rating", "Rating"),
        ("estrazioni", "delivery_data.orders", "Ordini"),
        ("estrazioni", "delivery_data.cancelled", "Cancellati"),
        ("estrazioni", "delivery_data.refunds", "Rimborsi"),
        ("estrazioni", "delivery_data.disputed", "Contestati"),
        ("estrazioni", "delivery_data.accepted", "Accettati"),
        ("estrazioni", "delivery_data.cancelled_refunds", "Rimb. annullati"),
        ("estrazioni", "delivery_data.opening_pct", "Apertura %"),
        ("estrazioni", "delivery_data.no_data", "Nessun dato trovato con i filtri selezionati."),
        ("estrazioni", "delivery_data.all_visible_stores", "Tutti gli store visibili"),
        ("estrazioni", "delivery_data.one_store_selected", "1 store selezionato"),
        ("estrazioni", "delivery_data.stores_selected", "store selezionati"),
        ("cash_statement", "ui.title", "Distinta cassa"),
        ("cash_statement", "ui.store", "Store"),
        ("cash_statement", "ui.date", "Data"),
        ("cash_statement", "ui.import_ipratico", "Import iPratico"),
        ("cash_statement", "ui.imported_value", "Dato importato"),
        ("cash_statement", "ui.turnover", "GIRO AFFARI"),
        ("cash_statement", "ui.expenses", "SPESE"),
        ("cash_statement", "ui.expenses_label", "Spese"),
        ("cash_statement", "ui.credit_notes", "Note credito"),
        ("cash_statement", "ui.cash_difference", "DIFFERENZA CASSA"),
        ("cash_statement", "ui.type", "Tipo"),
        ("cash_statement", "ui.amount", "Importo"),
        ("cash_statement", "ui.item", "Voce"),
        ("cash_statement", "ui.value", "Valore"),
        ("cash_statement", "ui.total", "Totale"),
        ("cash_statement", "ui.denomination", "Taglio"),
        ("cash_statement", "ui.qty", "Qta"),
        ("cash_statement", "ui.coins_total", "Monete totali"),
        ("cash_statement", "ui.select", "Seleziona..."),
        ("cash_statement", "ui.online", "ONLINE"),
        ("cash_statement", "ui.cash", "CONTANTI"),
        ("cash_statement", "ui.delete_statement", "Elimina distinta"),
        ("cash_statement", "ui.save", "Salva"),
        ("cash_statement", "ui.no_statement", "Nessuna distinta salvata per questa data."),
        ("cash_statement", "ui.no_photo", "Nessuna foto associata a questa giornata."),
        ("cash_statement", "ui.select_store_warning", "Seleziona prima uno store per usare la Distinta cassa."),
        ("cash_statement", "ui.deposit_1", "Distinta 1"),
        ("cash_statement", "ui.deposit_2", "Distinta 2"),
        ("cash_statement", "ui.locked_deposit_period", "Giornata inclusa nel periodo competenza di un versamento: le distinte contanti non sono modificabili e la distinta non e eliminabile."),
        ("cash_statement", "ui.locked_validated", "Giornata convalidata: solo l'amministratore puo modificare o eliminare la distinta di cassa."),
        ("cash_statement", "ui.locked_deposit_delete", "Giornata bloccata da versamento: non e possibile eliminare la distinta."),
        ("cash_statement", "ui.locked_validated_changes", "Giornata convalidata: modifiche consentite solo all'amministratore."),
        ("cash_statement", "ui.confirm_delete_photo", "Eliminare la foto associata a questa giornata?"),
        ("cash_statement", "ui.open_photo", "Apri foto"),
        ("cash_statement", "ui.delete_photo", "Elimina foto"),
        ("cash_statement", "ui.close", "Chiudi"),
        ("cash_statement", "ui.loading", "Caricamento..."),
        ("cash_statement", "ui.closing_photo", "Foto chiusure"),
        ("cash_statement", "ui.photo_load_error", "Impossibile caricare la foto."),
        ("cash_statement", "ui.delete_intro", "Stai per eliminare tutti i dati della distinta del giorno"),
        ("cash_statement", "ui.delete_photo_too", "Elimina anche la foto associata"),
        ("cash_statement", "ui.irreversible", "Operazione irreversibile."),
        ("cash_statement", "ui.cancel", "Annulla"),
        ("cash_statement", "ui.delete", "Elimina"),
        ("cash_statement", "ui.import_done_warnings", "Import iPratico completato con avvisi"),
        ("cash_statement", "ui.more_warnings", "altri avvisi"),
        ("cash_statement", "ui.import_error", "Errore import iPratico"),
        ("cash_statement", "ui.import_overwrite_confirm", "L'import iPratico sovrascrivera i campi chiusura importabili e sostituira Delivery/Coupon gia presenti. Continuare?"),
        ("cash_statement", "toolbar.rendiconto", "Rendiconto"),
        ("cash_statement", "toolbar.spese", "Spese"),
        ("cash_statement", "toolbar.versamenti", "Versamenti"),
        ("cash_statement", "toolbar.gestione_delivery", "Gestione delivery"),
        ("cash_statement", "toolbar.ricerca", "Ricerca"),
        ("rendiconto", "expenses.title", "Spese di cassa"),
        ("rendiconto", "expenses.no_store", "Seleziona uno store per inserire e visualizzare le spese."),
        ("rendiconto", "common.store", "Store"),
        ("rendiconto", "common.month", "Mese"),
        ("rendiconto", "common.go", "Vai"),
        ("rendiconto", "common.date", "Data"),
        ("rendiconto", "common.select", "Seleziona..."),
        ("rendiconto", "common.amount", "Importo"),
        ("rendiconto", "common.photo", "Foto"),
        ("rendiconto", "common.actions", "Azioni"),
        ("rendiconto", "common.total", "Totale"),
        ("rendiconto", "common.close", "Chiudi"),
        ("rendiconto", "common.cancel", "Annulla"),
        ("rendiconto", "common.delete", "Elimina"),
        ("rendiconto", "common.edit", "Modifica"),
        ("rendiconto", "common.save", "Salva"),
        ("rendiconto", "common.loading", "Caricamento..."),
        ("rendiconto", "common.photo_load_error", "Impossibile caricare la foto."),
        ("rendiconto", "expenses.new", "Nuova spesa"),
        ("rendiconto", "expenses.operation_type", "Tipo di operazione"),
        ("rendiconto", "expenses.receipt", "Scontrino"),
        ("rendiconto", "expenses.invoice", "Fattura"),
        ("rendiconto", "expenses.credit_note", "Nota di credito"),
        ("rendiconto", "expenses.supplier_expense", "Fornitore / Spesa"),
        ("rendiconto", "expenses.document", "Scontrino / Fattura"),
        ("rendiconto", "expenses.invoice_photo", "Foto fattura"),
        ("rendiconto", "expenses.save", "Salva spesa"),
        ("rendiconto", "expenses.month_summary", "Riepilogo mese"),
        ("rendiconto", "expenses.view_photo", "Vedi foto"),
        ("rendiconto", "expenses.delete_confirm", "Eliminare questa spesa?"),
        ("rendiconto", "expenses.empty", "Nessuna spesa trovata per il mese selezionato."),
        ("rendiconto", "expenses.edit", "Modifica spesa"),
        ("rendiconto", "expenses.replace_photo", "Sostituisci foto (opzionale)"),
        ("rendiconto", "expenses.replace_photo_help", "Se carichi un file, sostituisce la foto associata a questa spesa."),
        ("rendiconto", "expenses.save_changes", "Salva modifiche"),
        ("rendiconto", "expenses.photo_title", "Foto spesa"),
        ("rendiconto", "deposits.title", "Versamenti"),
        ("rendiconto", "deposits.no_store", "Seleziona uno store per inserire e visualizzare i versamenti."),
        ("rendiconto", "deposits.new", "Nuovo versamento"),
        ("rendiconto", "deposits.deposit_date", "Data versamento"),
        ("rendiconto", "deposits.period_from_full", "Periodo competenza - Dal"),
        ("rendiconto", "deposits.period_to_full", "Periodo competenza - Al"),
        ("rendiconto", "deposits.full_name", "Nome e cognome"),
        ("rendiconto", "deposits.type", "Tipo versamento"),
        ("rendiconto", "deposits.operator", "Operatore"),
        ("rendiconto", "deposits.card", "Tessera"),
        ("rendiconto", "deposits.bank_reference", "Nome banca e distinta"),
        ("rendiconto", "deposits.period_total", "Totale distinte periodo"),
        ("rendiconto", "deposits.difference", "Differenza"),
        ("rendiconto", "deposits.photo_label", "Foto distinta"),
        ("rendiconto", "deposits.photo_help", "Carica la foto della ricevuta di versamento, salvo una delle dichiarazioni sotto."),
        ("rendiconto", "deposits.no_receipt", "Dichiaro che lo sportello non ha emesso la ricevuta"),
        ("rendiconto", "deposits.lost_receipt", "Ricevuta smarrita"),
        ("rendiconto", "deposits.save", "Salva versamento"),
        ("rendiconto", "deposits.total", "Totale versamenti"),
        ("rendiconto", "deposits.delete_confirm", "Eliminare questo versamento?"),
        ("rendiconto", "deposits.period", "Periodo"),
        ("rendiconto", "deposits.reference_short", "Rif."),
        ("rendiconto", "deposits.empty", "Nessun versamento trovato per il mese selezionato."),
        ("rendiconto", "deposits.edit", "Modifica versamento"),
        ("rendiconto", "deposits.period_from", "Periodo - Dal"),
        ("rendiconto", "deposits.period_to", "Periodo - Al"),
        ("rendiconto", "deposits.reference", "Riferimento versamento"),
        ("rendiconto", "deposits.photo_required_help", "Se non c'e gia una foto associata, il caricamento e obbligatorio salvo una delle dichiarazioni sotto."),
        ("rendiconto", "deposits.photo_title", "Foto versamento"),
        ("rendiconto", "deposits.adjust_title", "Correzione distinte per chiudere la differenza"),
        ("rendiconto", "deposits.locked", "Bloccata"),
        ("rendiconto", "deposits.action", "Azione"),
        ("rendiconto", "deposits.adjust_help", "Seleziona una o piu giornate da correggere: modifica i tagli/monete finche la differenza torna a zero."),
        ("rendiconto", "deposits.day", "Giornata"),
        ("rendiconto", "deposits.day_total", "Totale giornata"),
        ("rendiconto", "deposits.select_day", "Seleziona una giornata per modificare le distinte."),
        ("rendiconto", "deposits.coins", "Monete"),
        ("rendiconto", "deposits.short_total", "Tot."),
        ("rendiconto", "deposits.save_day", "Salva giornata"),
        ("rendiconto", "delivery.title", "Gestione delivery"),
        ("rendiconto", "delivery.sales", "Vendite"),
        ("rendiconto", "delivery.import_statement", "Importa distinta"),
        ("rendiconto", "delivery.import_help", "I pagamenti vengono importati dalla distinta cassa della settimana selezionata ma restano modificabili prima del salvataggio."),
        ("rendiconto", "delivery.payment_online", "Pagamento online"),
        ("rendiconto", "delivery.payment_cash", "Pagamento contanti"),
        ("rendiconto", "delivery.orders", "Numero ordini"),
        ("rendiconto", "delivery.cancelled_orders", "Ordini cancellati"),
        ("rendiconto", "delivery.refunds", "Rimborsi"),
        ("rendiconto", "delivery.refund_value", "Valore rimborsi"),
        ("rendiconto", "delivery.refund_disputes", "Contestazioni dei rimborsi"),
        ("rendiconto", "delivery.accepted_disputes", "Contestazioni accettate"),
        ("rendiconto", "delivery.cancelled_refund_value", "Valore rimborsi annullati"),
        ("rendiconto", "delivery.refund_pct", "% Rimborsi su ordini"),
        ("rendiconto", "delivery.refund_pct_net", "% Rimborsi su ordini (netto contestazioni)"),
        ("rendiconto", "delivery.opening", "Apertura"),
        ("rendiconto", "delivery.potential_sales", "Vendite potenziali stimate"),
        ("rendiconto", "delivery.lost_sales", "Vendite perse stimate"),
        ("rendiconto", "delivery.rating", "Rating"),
        ("rendiconto", "delivery.weekly_rating", "Rating settimanale"),
        ("rendiconto", "delivery.prev_week_comparison", "Confronto settimana precedente"),
        ("rendiconto", "delivery.no_store", "Seleziona uno store per inserire e visualizzare i dati."),
        ("rendiconto", "delivery.week", "Settimana"),
        ("rendiconto", "delivery.select_store", "Seleziona uno store per procedere."),
        ("rendiconto", "delivery.no_provider", "Nessun provider delivery attivo per questo tenant."),
        ("rendiconto", "delivery.import_all", "Importa pagamenti da distinta"),
        ("rendiconto", "delivery.save_all", "Salva dati delivery"),
        ("rendiconto", "search.title", "Ricerca"),
        ("rendiconto", "search.visible_stores", "Store visibili"),
        ("rendiconto", "search.kind", "Cosa cercare"),
        ("rendiconto", "search.period", "Periodo"),
        ("rendiconto", "search.week", "Settimana"),
        ("rendiconto", "search.month", "Mese"),
        ("rendiconto", "search.date_in_week", "Data (nella settimana)"),
        ("rendiconto", "search.from", "Dal"),
        ("rendiconto", "search.to", "Al"),
        ("rendiconto", "search.search", "Cerca"),
        ("rendiconto", "search.export", "Estrai Excel (vista corrente)"),
        ("rendiconto", "search.filter_by", "Filtra per"),
        ("rendiconto", "search.all_fields", "Tutti i campi"),
        ("rendiconto", "search.placeholder", "Scrivi per filtrare..."),
        ("rendiconto", "search.reset", "Reset"),
        ("rendiconto", "search.hint", "Suggerimento: scegli il campo dalla tendina e digita il valore per filtrare i risultati."),
        ("rendiconto", "search.ui_error", "Errore UI"),
        ("rendiconto", "search.rows", "Righe"),
        ("rendiconto", "search.of", "su"),
        ("rendiconto", "search.no_results", "Nessun risultato."),
        ("rendiconto", "search.store_unavailable", "store non disponibile"),
        ("rendiconto", "search.invalid_range", "Seleziona un intervallo valido."),
        ("rendiconto", "search.search_error", "Errore ricerca"),
        ("rendiconto", "search.export_error", "Errore export"),
        ("warehouse", "warehouse.mov_type.inv", "Inventario"),
        ("warehouse", "warehouse.mov_type.txin", "Trasferimenti In"),
        ("warehouse", "warehouse.mov_type.txout", "Trasferimenti Out"),
        ("warehouse", "warehouse.mov_type.waste_crudo", "Waste Crudo"),
        ("warehouse", "toolbar.title", "Magazzino"),
        ("warehouse", "toolbar.analysis", "Analisi"),
        ("warehouse", "toolbar.consumption", "Consumi"),
        ("warehouse", "toolbar.delivery_entry", "Inserimento DDT"),
        ("warehouse", "toolbar.expense", "Spesa"),
        ("warehouse", "toolbar.delivery_edit", "Modifica DDT"),
        ("warehouse", "toolbar.inventory_data", "Dati Inventario"),
        ("warehouse", "toolbar.inventory_edit", "Modifica Dati Inventario"),
        ("warehouse", "toolbar.search", "Ricerca"),
        ("warehouse", "toolbar.orders", "Ordini"),
        ("warehouse", "home.title", "Magazzino"),
        ("warehouse", "home.intro", "Seleziona una funzione dalla barra qui sopra per iniziare."),
        ("warehouse", "analysis.title", "Analisi"),
        ("warehouse", "analysis.period_type", "Tipo periodo"),
        ("warehouse", "analysis.week_option", "Settimana"),
        ("warehouse", "analysis.month_option", "Mese"),
        ("warehouse", "analysis.period_option", "Periodo"),
        ("warehouse", "analysis.select_date", "Seleziona una data"),
        ("warehouse", "analysis.select_month", "Seleziona mese"),
        ("warehouse", "analysis.range", "Intervallo"),
        ("warehouse", "analysis.apply", "Applica"),
        ("warehouse", "analysis.total_store", "Totale store"),
        ("warehouse", "analysis.revenues_net", "Revenues (net)"),
        ("warehouse", "analysis.by_supplier", "Per fornitore"),
        ("warehouse", "analysis.supplier", "Fornitore"),
        ("warehouse", "analysis.select_supplier", "Seleziona..."),
        ("warehouse", "analysis.item", "Voce"),
        ("warehouse", "analysis.foodpaper", "FoodPaper"),
        ("warehouse", "analysis.operating", "Operating"),
        ("warehouse", "analysis.initial_inventory", "Inventario iniziale"),
        ("warehouse", "analysis.delivery", "Delivery"),
        ("warehouse", "analysis.transfers_in", "Trasferimenti In"),
        ("warehouse", "analysis.transfers_out", "Trasferimenti Out"),
        ("warehouse", "analysis.final_inventory", "Inventario finale"),
        ("warehouse", "analysis.raw_waste", "Waste (crudo)"),
        ("warehouse", "analysis.consumption", "Consumo"),
        ("warehouse", "analysis.consumption_pct", "Consumo %"),
        ("warehouse", "analysis.waste_pct", "Waste %"),
        ("warehouse", "analysis.total", "Totale"),
        ("warehouse", "analysis.loading", "Caricamento..."),
        ("warehouse", "analysis.error", "Errore"),
        ("warehouse", "analysis.select_period", "Seleziona un periodo e premi \"Applica\"."),
        ("warehouse", "analysis.select_supplier_help", "Seleziona un fornitore per vedere i KPI dedicati."),
        ("warehouse", "common.store", "Store"),
        ("warehouse", "common.price_list", "Listino"),
        ("warehouse", "common.export_excel", "Esporta Excel"),
        ("warehouse", "riepilogo.title", "Dati Magazzino"),
        ("warehouse", "riepilogo.subtitle", "Riepilogo mensile (valori in euro)"),
        ("warehouse", "riepilogo.summary_table", "Tabella riepilogativa"),
        ("warehouse", "riepilogo.total_revenues_net", "Revenues (net) totali"),
        ("warehouse", "riepilogo.select_prompt", "Seleziona mese e listino, poi premi \"Applica\"."),
        ("warehouse", "consumi.title", "Consumi"),
        ("warehouse", "consumi.subtitle", "Consumo in pezzi per prodotto"),
        ("warehouse", "consumi.week_option", "Settimana (lun - dom)"),
        ("warehouse", "consumi.period_revenues_net", "Revenues (net) periodo"),
        ("warehouse", "consumi.product_detail", "Dettaglio prodotti"),
        ("warehouse", "consumi.mobile_hint", "Tocca un prodotto per vedere i dettagli."),
        ("warehouse", "consumi.show_details", "Mostra dettagli"),
        ("warehouse", "consumi.hide_details", "Nascondi dettagli"),
        ("warehouse", "consumi.description", "Descrizione"),
        ("warehouse", "consumi.no_data", "Nessun dato per i filtri selezionati."),
        ("warehouse", "consumi.supplier_prompt", "Seleziona un fornitore e premi \"Applica\"."),
        ("warehouse", "consumi.select_prompt", "Seleziona un periodo, un fornitore e premi \"Applica\"."),
        ("warehouse", "common.save", "Salva"),
        ("warehouse", "common.total_eur", "Tot EUR"),
        ("warehouse", "delivery_new.title", "Inserimento DDT"),
        ("warehouse", "delivery_edit.title", "Modifica DDT"),
        ("warehouse", "delivery.document_date", "Data documento"),
        ("warehouse", "delivery.delivery_date", "Data consegna"),
        ("warehouse", "delivery.select_supplier", "Seleziona un fornitore..."),
        ("warehouse", "delivery.load_price_list", "Carica listino"),
        ("warehouse", "delivery.search_product_placeholder", "Cerca prodotto (descrizione)..."),
        ("warehouse", "delivery_new.partial_total", "Totale parziale inserimento"),
        ("warehouse", "delivery_edit.partial_total", "Totale parziale DDT"),
        ("warehouse", "delivery.group", "Gruppo"),
        ("warehouse", "delivery.col_description", "Descrizione"),
        ("warehouse", "delivery.col_price", "Prezzo"),
        ("warehouse", "delivery.col_pack_qty", "Qta car"),
        ("warehouse", "delivery.col_inner_qty", "Qta int"),
        ("warehouse", "delivery.no_amount_entered", "Nessun importo inserito"),
        ("warehouse", "delivery.no_product_found", "Nessun prodotto trovato"),
        ("warehouse", "delivery.packages", "Colli"),
        ("warehouse", "delivery.pieces", "Pezzi"),
        ("warehouse", "delivery.pieces_kg", "Pezzi/Kg"),
        ("warehouse", "delivery.ddt_price", "Prezzo DDT"),
        ("warehouse", "delivery.discount", "Sconto"),
        ("warehouse", "delivery.discount_pct", "Sconto %"),
        ("warehouse", "delivery.total_qty", "Q.tà totale"),
        ("warehouse", "delivery.value", "Valore"),
        ("warehouse", "delivery.restore_list_price", "Ripristina prezzo di listino"),
        ("warehouse", "delivery.product", "Prodotto"),
        ("warehouse", "delivery.list_price", "Prezzo listino"),
        ("warehouse", "delivery.unit", "Unità"),
        ("warehouse", "delivery_new.help", "I campi da compilare per il DDT sono: Colli, Pezzi (se necessario), Prezzo DDT (se diverso dal listino), Sconto %. Le colonne quantità totale e valore vengono calcolate automaticamente."),
        ("warehouse", "delivery.no_item_for_supplier", "Nessun articolo trovato per il fornitore selezionato."),
        ("warehouse", "delivery.no_group", "(Senza gruppo)"),
        ("warehouse", "delivery_new.required_dates", "Compila \"Data documento\" e \"Data consegna\" prima di salvare il DDT."),
        ("warehouse", "delivery.invalid_packages_prefix", "Valore \"Colli\" non valido nella riga:"),
        ("warehouse", "delivery.invalid_pieces_prefix", "Valore \"Pezzi\" non valido nella riga:"),
        ("warehouse", "delivery.invalid_number_help", "Usa solo numeri (decimali con , o .)."),
        ("warehouse", "delivery.row", "Riga"),
        ("warehouse", "delivery_new.no_qty_confirm", "Non hai inserito quantità su nessuna riga. Vuoi proseguire comunque con il salvataggio?"),
        ("warehouse", "delivery_new.save_intro", "Stai per salvare il seguente DDT:"),
        ("warehouse", "delivery.compiled_rows", "Righe compilate"),
        ("warehouse", "delivery.confirm_save", "Confermi il salvataggio?"),
        ("warehouse", "delivery.confirm", "Confermi?"),
        ("warehouse", "delivery.without_description", "(Senza descrizione)"),
        ("warehouse", "delivery.factor", "fattore"),
        ("warehouse", "delivery.rows_found", "Righe trovate"),
        ("warehouse", "inventory_new.title", "Dati Inventario"),
        ("warehouse", "inventory_edit.title", "Modifica Dati Inventario"),
        ("warehouse", "inventory.movement_date", "Data movimentazione"),
        ("warehouse", "inventory.suppliers", "Fornitori"),
        ("warehouse", "inventory.select_suppliers", "Seleziona fornitori..."),
        ("warehouse", "inventory.select_all", "Seleziona tutto"),
        ("warehouse", "inventory.deselect_all", "Deseleziona tutto"),
        ("warehouse", "inventory.select_one_or_more_suppliers", "Seleziona uno o più fornitori."),
        ("warehouse", "inventory.select_at_least_one_supplier", "Seleziona almeno un fornitore."),
        ("warehouse", "inventory.suppliers_selected", "fornitori selezionati"),
        ("warehouse", "inventory.partial_total", "Totale parziale inventario"),
        ("warehouse", "inventory.site2_transfer", "SITE2 (trasferimento)"),
        ("warehouse", "inventory.select_store", "Seleziona store"),
        ("warehouse", "inventory.select_store_from", "Seleziona lo store DA CUI arriva la merce (SITE2)."),
        ("warehouse", "inventory.select_store_to", "Seleziona lo store VERSO CUI va la merce (SITE2)."),
        ("warehouse", "inventory.select_store_option", "Seleziona store..."),
        ("warehouse", "inventory.store_load_error", "Errore caricamento store"),
        ("warehouse", "common.loading_error", "Errore caricamento"),
        ("warehouse", "inventory.required_save_fields", "Compila Data movimentazione, Tipo movimentazione e seleziona almeno un Fornitore."),
        ("warehouse", "inventory.no_rows_with_qty", "Nessuna riga con quantità inserite."),
        ("warehouse", "inventory.store_selection_cancelled", "Selezione store annullata: salvataggio non eseguito."),
        ("warehouse", "inventory.saved", "Salvato."),
        ("warehouse", "inventory.inserted_inventory_rows", "Righe inserite inventario"),
        ("warehouse", "inventory.inserted_tx_rows", "Righe inserite TX"),
        ("warehouse", "inventory.skipped_rows", "Righe ignorate"),
        ("warehouse", "inventory.required_pdf_fields", "Per esportare in PDF compila Data movimentazione, Tipo movimentazione e seleziona almeno un Fornitore."),
        ("warehouse", "inventory.no_rows_to_export", "Nessuna riga da esportare."),
        ("warehouse", "inventory.pdf_export_error", "Errore export PDF"),
        ("warehouse", "inventory.conversion_kg_pz", "Conv (KG -> PZ)"),
        ("warehouse", "inventory.accumulated", "Accumulato"),
        ("warehouse", "inventory.delete_row", "Elimina riga"),
        ("warehouse", "inventory.delete", "Elimina"),
        ("warehouse", "inventory_edit.operational_notes", "Note operative"),
        ("warehouse", "inventory_edit.help_delete_row", "Per eliminare una riga usa x: la cancellazione e immediata in DB."),
        ("warehouse", "inventory_edit.help_quantities", "Le modifiche alle quantita (CAR/INT/PEZ/KG) si salvano con Salva, solo sulle righe cambiate."),
        ("warehouse", "inventory_edit.help_site2", "Per TXIN/TXOUT devi selezionare SITE2 prima di caricare o salvare."),
        ("warehouse", "inventory_edit.change_header_title", "Cambio intestazione movimento"),
        ("warehouse", "inventory_edit.change_header_subtitle", "Aggiorna tutte le righe del movimento"),
        ("warehouse", "inventory_edit.new_date", "Nuova data"),
        ("warehouse", "inventory_edit.new_type", "Nuovo tipo"),
        ("warehouse", "inventory_edit.new_site2", "Nuovo SITE2"),
        ("warehouse", "inventory_edit.change_header_help", "Per i trasferimenti (TXIN/TXOUT) puoi anche correggere mittente/destinatario cambiando tipo e/o SITE2."),
        ("warehouse", "inventory_edit.current_total_pieces", "Totpz (attuale)"),
        ("warehouse", "inventory_edit.current_total_eur", "Tot EUR (attuale)"),
        ("warehouse", "inventory_edit.missing_header", "Dati header mancanti: data, tipo e fornitore."),
        ("warehouse", "inventory_edit.site2_required", "Per TXIN/TXOUT devi selezionare SITE2."),
        ("warehouse", "inventory_edit.site2_required_save", "Per TXIN/TXOUT devi selezionare SITE2 prima di salvare."),
        ("warehouse", "inventory_edit.missing_row_identity", "Descrizione o codice non disponibili: impossibile eliminare la riga."),
        ("warehouse", "inventory_edit.required_save", "Compila fornitore, tipo e data prima di salvare."),
        ("warehouse", "inventory_edit.no_changed_rows", "Nessuna riga modificata: nulla da salvare."),
        ("warehouse", "inventory_edit.confirm_save_changes", "Confermi il salvataggio delle modifiche?"),
        ("warehouse", "inventory_edit.changed_rows", "Righe modificate"),
        ("warehouse", "inventory_edit.other_rows", "altre righe"),
        ("warehouse", "inventory_edit.select_new_date", "Seleziona la nuova data prima di applicare."),
        ("warehouse", "inventory_edit.confirm_change_header", "Applico la modifica di intestazione a tutte le righe del movimento?"),
        ("warehouse", "inventory_edit.required_pdf_fields", "Per esportare in PDF compila fornitore, tipo e data."),
        ("warehouse", "delivery_edit.help", "Modifica colli/pezzi e premi Salva modifiche. Le righe eliminate con la X vengono cancellate subito."),
        ("warehouse", "delivery_edit.change_dates_title", "Cambio date per tutto il DDT"),
        ("warehouse", "delivery_edit.new_document_date", "Nuova data documento (Fattura)"),
        ("warehouse", "delivery_edit.new_delivery_date", "Nuova data consegna (Data)"),
        ("warehouse", "delivery_edit.apply_date_change", "Applica cambio date"),
        ("warehouse", "delivery_edit.no_rows", "Nessuna riga trovata per i parametri inseriti."),
        ("warehouse", "delivery.calculated_qty", "Qta (calcolata)"),
        ("warehouse", "delivery_edit.missing_delete_data", "Dati mancanti per eliminare la riga."),
        ("warehouse", "delivery_edit.confirm_delete_row_prefix", "Confermi eliminazione della riga"),
        ("warehouse", "delivery_edit.delete_row_error", "Errore eliminazione riga."),
        ("warehouse", "delivery_edit.delete_network_error", "Errore rete durante eliminazione:"),
        ("warehouse", "delivery_edit.required_header", "Compila fornitore e date prima di salvare."),
        ("warehouse", "delivery_edit.save_changes_intro", "Stai per salvare le modifiche del DDT:"),
        ("warehouse", "delivery_edit.changed_rows", "Righe modificate"),
        ("warehouse", "delivery_edit.no_changed_rows", "Nessuna riga modificata. Vuoi procedere comunque?"),
        ("warehouse", "delivery_edit.required_new_dates", "Compila le nuove date prima di applicare."),
        ("warehouse", "delivery_edit.confirm_change_dates", "Confermi cambio date su tutte le righe del DDT?"),
        ("warehouse", "search.title", "Ricerca Magazzino"),
        ("warehouse", "search.heading", "Ricerca movimentazioni"),
        ("warehouse", "search.from", "Da"),
        ("warehouse", "search.to", "A"),
        ("warehouse", "search.movement_type", "Tipo movimentazione"),
        ("warehouse", "search.all", "Tutte"),
        ("warehouse", "search.inventory", "Inventario"),
        ("warehouse", "search.raw_waste", "Waste crudo"),
        ("warehouse", "search.transfer_in", "Trasferimento IN"),
        ("warehouse", "search.transfer_out", "Trasferimento OUT"),
        ("warehouse", "search.search", "Cerca"),
        ("warehouse", "search.type", "Tipo"),
        ("warehouse", "search.date", "Data"),
        ("warehouse", "search.supplier", "Fornitore"),
        ("warehouse", "search.details", "Dettagli"),
        ("warehouse", "search.open", "Apri"),
        ("warehouse", "search.empty", "Nessun risultato."),
        ("warehouse", "search.invalid_range", "Intervallo non valido: la data di fine e precedente alla data di inizio."),
        ("warehouse", "search.range_too_large", "Intervallo troppo ampio: la ricerca e limitata a massimo 1 mese."),
        ("dashboard", "title", "Raccolta dati"),
        ("dashboard", "previous_month", "Mese precedente"),
        ("dashboard", "next_month", "Mese successivo"),
        ("dashboard", "weekday.mon", "Lun"),
        ("dashboard", "weekday.tue", "Mar"),
        ("dashboard", "weekday.wed", "Mer"),
        ("dashboard", "weekday.thu", "Gio"),
        ("dashboard", "weekday.fri", "Ven"),
        ("dashboard", "weekday.sat", "Sab"),
        ("dashboard", "weekday.sun", "Dom"),
        ("dashboard", "mode", "Seleziona modalita dashboard"),
        ("dashboard", "cash_report", "Rendiconto"),
        ("dashboard", "warehouse", "Magazzino"),
        ("dashboard", "total_ddt", "Totale DDT"),
        ("dashboard", "transfers_in", "Trasferimenti IN"),
        ("dashboard", "transfers_out", "Trasferimenti OUT"),
        ("dashboard", "waste", "Waste"),
        ("dashboard", "turnover", "Giro affari"),
        ("dashboard", "receipts", "Scontrini"),
        ("dashboard", "cash_deposits_total", "Tot distinte"),
        ("dashboard", "cancelled", "Annullati"),
        ("dashboard", "cash_difference", "Diff. cassa"),
        ("dashboard", "last_deposit_date", "Ultima data versata"),
        ("dashboard", "unpaid_days", "Giorni non versati"),
        ("dashboard", "amount_to_deposit", "Totale da versare"),
        ("dashboard", "closing.gross_sales", "Vendite lorde"),
        ("dashboard", "pos", "POS"),
        ("dashboard", "ticket", "Ticket"),
        ("dashboard", "coupon", "Coupon"),
        ("dashboard", "invoices", "Fatture"),
        ("dashboard", "invoice_count", "Numero fatture"),
        ("dashboard", "freebies", "Omaggi"),
        ("dashboard", "sales_vat_4", "Vendite IVA 4%"),
        ("dashboard", "sales_vat_22", "Vendite IVA 22%"),
        ("dashboard", "expenses", "Spese"),
        ("dashboard", "validate_period", "Convalida periodo"),
        ("dashboard", "validated_periods", "Periodi convalidati"),
        ("dashboard", "total_cash_difference", "Diff. cassa totale"),
        ("dashboard", "requested_by", "Richiesto da"),
        ("dashboard", "role", "Ruolo"),
        ("dashboard", "created_at", "Creato il"),
        ("dashboard", "delete_validation_confirm", "Vuoi eliminare questa convalida? I giorni torneranno modificabili per user e supervisor."),
        ("dashboard", "no_validated_periods", "Nessun periodo convalidato per questo store."),
        ("dashboard", "details", "Dettaglio"),
        ("dashboard", "filtered_total", "Totale (filtrato)"),
        ("dashboard", "filter_label", "Filtro"),
        ("dashboard", "total_all", "Totale (tutto)"),
        ("dashboard", "supplier", "Fornitore"),
        ("dashboard", "cash_difference_full", "Differenza di cassa"),
        ("dashboard", "cash_deposit_sum", "Somma distinte"),
        ("dashboard", "delivery_online", "Delivery Online"),
        ("dashboard", "delivery_cash", "Delivery Contanti"),
        ("dashboard", "expenses_net", "Totale spese (net)"),
        ("dashboard", "expenses_total", "Spese totali"),
        ("dashboard", "credit_notes", "Note credito"),
        ("dashboard", "closing_data", "Dati chiusura"),
        ("dashboard", "custom_cash_sections", "Personalizzazioni rendiconto"),
        ("dashboard", "daily_summary", "Riepilogo giornata"),
        ("dashboard", "hello", "Ciao"),
        ("dashboard", "daily_summary_intro", "ecco i dati della giornata:"),
        ("dashboard", "budget", "BUDGET"),
        ("dashboard", "ly_revenues", "VENDITE ANNO PRECEDENTE"),
        ("dashboard", "forecast", "PREVISIONE"),
        ("dashboard", "ly_date_comparison", "Confronto su data LY"),
        ("dashboard", "active_cash_customizations", "Personalizzazioni rendiconto attive"),
        ("dashboard", "good_work", "Buon lavoro!"),
        ("dashboard", "deposits_alert", "Attenzione versamenti"),
        ("dashboard", "validate_warning", "Stai per convalidare il periodo selezionato. L'operazione e irreversibile e, dopo la conferma, le distinte di quei giorni non saranno piu modificabili da user e supervisor."),
        ("dashboard", "period", "Periodo"),
        ("dashboard", "cash_differences_sum", "Somma differenze di cassa"),
        ("dashboard", "day", "Giorno"),
        ("dashboard", "confirm_validation", "Conferma convalida"),
        ("dashboard", "error", "Errore"),
        ("dashboard", "non_json_response", "Risposta non JSON (probabile redirect/login/store)."),
        ("dashboard", "no_days_in_period", "Nessun giorno nel periodo selezionato."),
        ("dashboard", "select_valid_period", "Seleziona un periodo valido."),
        ("dashboard", "preview_load_error", "Errore caricamento anteprima."),
        ("dashboard", "preview_unavailable", "Anteprima non disponibile."),
        ("dashboard", "validation_error", "Errore convalida periodo."),
        ("dashboard", "no_data_for_day", "Nessun dato per questo giorno."),
        ("dashboard", "destination", "Destinazione"),
        ("dashboard", "site2_missing", "SITE2 non indicato"),
        ("dashboard", "no_closing_data", "Nessun dato chiusura per questo giorno."),
        ("dashboard", "custom_section", "Personalizzazione"),
        ("links", "title", "Link"),
        ("links", "empty_category", "Nessun link in questa categoria."),
        ("links", "empty", "Nessun link configurato."),
        ("orari", "toolbar.title", "Gestione Orari"),
        ("orari", "toolbar.registry", "Anagrafica"),
        ("orari", "toolbar.schedules", "Orari"),
        ("orari", "staff.title", "Anagrafica Staff"),
        ("orari", "staff.full_name", "Nome e Cognome"),
        ("orari", "staff.employment_type", "Inquadramento"),
        ("orari", "staff.select", "Seleziona..."),
        ("orari", "staff.contract_hours", "Ore Contrattuali"),
        ("orari", "staff.employee_code", "Codice Dipendente"),
        ("orari", "staff.scheduling", "Scheduling"),
        ("orari", "staff.add", "Aggiungi"),
        ("orari", "staff.status", "Stato"),
        ("orari", "staff.actions", "Azioni"),
        ("orari", "staff.active", "Attivo"),
        ("orari", "staff.inactive", "Inattivo"),
        ("orari", "staff.save", "Salva"),
        ("orari", "staff.deactivate", "Disattiva"),
        ("orari", "staff.activate", "Attiva"),
        ("orari", "staff.delete", "Elimina"),
        ("orari", "staff.delete_confirm", "Eliminare definitivamente questa persona?"),
        ("orari", "staff.empty", "Nessuna persona presente."),
        ("orari", "legend.title", "Legenda colori (Orari)"),
        ("orari", "legend.help", "Crea una legenda per i colori utilizzati nella pagina Orari."),
        ("orari", "legend.name", "Nome legenda"),
        ("orari", "legend.color", "Colore"),
        ("orari", "legend.pick_color", "Scegli colore"),
        ("orari", "legend.select_color", "Seleziona colore legenda"),
        ("orari", "legend.color_help", "Scegli uno dei colori disponibili nella pagina Orari."),
        ("orari", "legend.save", "Salva legenda"),
        ("orari", "legend.actions", "Azioni"),
        ("orari", "legend.delete", "Elimina"),
        ("orari", "legend.delete_confirm", "Eliminare questa legenda?"),
        ("orari", "legend.empty", "Nessuna legenda presente."),
        ("orari", "schedule.title", "Gestione Orari - Orari"),
        ("orari", "schedule.people", "Persone"),
        ("orari", "schedule.search_placeholder", "Cerca..."),
        ("orari", "schedule.all", "Tutti"),
        ("orari", "schedule.none", "Nessuno"),
        ("orari", "schedule.current_week", "Settimana corrente"),
        ("orari", "schedule.import_week", "Importa settimana"),
        ("orari", "schedule.import_week_title", "Sovrascrive gli orari della settimana corrente"),
        ("orari", "schedule.show_linear", "Mostra lineare"),
        ("orari", "schedule.pdf", "PDF"),
        ("orari", "schedule.save", "Salva"),
        ("orari", "schedule.total_week_sales", "Fatturato totale settimana"),
        ("orari", "schedule.total_hours", "Ore totali"),
        ("orari", "schedule.productivity", "Produttivita"),
        ("orari", "schedule.color_legend", "Legenda colori:"),
        ("orari", "schedule.warning", "Attenzione"),
        ("orari", "schedule.contract_anomalies", "Anomalie ore contrattuali"),
        ("orari", "schedule.close", "Chiudi"),
        ("orari", "schedule.unsaved_changes", "Modifiche non salvate"),
        ("orari", "schedule.unsaved_pdf_text", "Ci sono modifiche non salvate. Vuoi salvarle prima di generare il PDF?"),
        ("orari", "schedule.cancel", "Annulla"),
        ("orari", "schedule.generate_without_save", "Genera senza salvare"),
        ("orari", "schedule.save_and_generate_pdf", "Salva e genera PDF"),
        ("orari", "schedule.import_week_help", "Seleziona una data nella settimana da copiare. Gli orari della settimana corrente verranno sovrascritti."),
        ("orari", "schedule.source_week", "Settimana sorgente"),
        ("orari", "schedule.import", "Importa"),
        ("orari", "schedule.cell_color", "Colore cella"),
        ("orari", "schedule.copy_day", "Copia giornata"),
        ("orari", "schedule.paste", "Incolla"),
        ("orari", "schedule.clear_copy", "Svuota copia"),
        ("orari", "schedule.linear", "Lineare"),
        ("orari", "schedule.day", "Giorno"),
        ("orari", "schedule.net_forecast", "Previsione netta"),
        ("orari", "schedule.net_week_forecast", "Previsione netta settimana"),
        ("orari", "schedule.forecasts", "Previsioni"),
        ("orari", "schedule.previous_year_net", "Anno precedente netto"),
        ("orari", "schedule.previous_year_short", "Anno prec."),
        ("orari", "schedule.aligned_to", "Allineato a"),
        ("orari", "schedule.person_name", "Nominativo"),
        ("orari", "schedule.select_person_warning", "Seleziona almeno una persona."),
        ("orari", "schedule.loading", "Caricamento..."),
        ("orari", "schedule.load_error", "Errore caricamento."),
        ("orari", "schedule.required_field", "Campo obbligatorio"),
        ("orari", "schedule.sales_forecast_required", "Inserisci la previsione vendite per tutti i giorni della settimana."),
        ("orari", "schedule.week_data_unavailable", "Dati settimana non disponibili."),
        ("orari", "schedule.people_count", "Persone"),
        ("orari", "schedule.row_color", "Colore riga"),
        ("orari", "schedule.reason", "Causale"),
        ("orari", "schedule.loan_store", "Store prestito"),
        ("orari", "weekday.mon", "LUN"),
        ("orari", "weekday.tue", "MAR"),
        ("orari", "weekday.wed", "MER"),
        ("orari", "weekday.thu", "GIO"),
        ("orari", "weekday.fri", "VEN"),
        ("orari", "weekday.sat", "SAB"),
        ("orari", "weekday.sun", "DOM"),
        ("cruscotto", "toolbar.title", "Cruscotto"),
        ("cruscotto", "toolbar.weekly_analysis", "Analisi Settimanale"),
        ("cruscotto", "toolbar.weekly_kpi_analysis", "Analisi KPI Settimanale"),
        ("cruscotto", "toolbar.monthly_analysis", "Analisi Mensile"),
        ("cruscotto", "toolbar.pnl_store", "P&L store"),
        ("cruscotto", "weekly.title", "Cruscotto - Analisi Settimanale"),
        ("cruscotto", "weekly.heading", "Analisi Settimanale"),
        ("cruscotto", "weekly.select_year", "Seleziona anno"),
        ("cruscotto", "weekly.select_week", "Seleziona settimana"),
        ("cruscotto", "weekly.week", "Settimana"),
        ("cruscotto", "weekly.today", "Oggi"),
        ("cruscotto", "weekly.overview", "Panoramica"),
        ("cruscotto", "weekly.detail", "Dettaglio settimana"),
        ("cruscotto", "monthly.title", "Cruscotto - Analisi Mensile"),
        ("cruscotto", "monthly.heading", "Analisi Mensile"),
        ("cruscotto", "monthly.select_year", "Seleziona anno"),
        ("cruscotto", "monthly.select_month", "Seleziona mese"),
        ("cruscotto", "monthly.month", "Mese"),
        ("cruscotto", "monthly.today", "Oggi"),
        ("cruscotto", "monthly.overview", "Panoramica"),
        ("cruscotto", "monthly.detail", "Dettaglio mese"),
        ("cruscotto", "charts", "Grafici"),
        ("cruscotto", "revenues", "Revenues"),
        ("cruscotto", "receipt", "Receipt"),
        ("cruscotto", "average_receipt", "Average Receipt"),
        ("cruscotto", "actual_vs_ly", "Actual vs Last Year"),
        ("cruscotto", "weekly_revenues_help", "Actual (o previsione se mancante) vs Last Year vs Budget"),
        ("cruscotto", "monthly_revenues_help", "Proiezione (Actual/Previsione) vs Last Year/Budget"),
        ("cruscotto", "overview", "Overview"),
        ("cruscotto", "week_detail", "Week detail"),
        ("cruscotto", "month_detail", "Month detail"),
        ("cruscotto", "actual", "Actual"),
        ("cruscotto", "forecast", "Previsione"),
        ("cruscotto", "previous_short", "Prev."),
        ("cruscotto", "projection", "Proiezione"),
        ("cruscotto", "projection_short", "Proj"),
        ("cruscotto", "partial", "parziale"),
        ("cruscotto", "last_year", "Last Year"),
        ("cruscotto", "last_year_short", "LY"),
        ("cruscotto", "day", "Giorno"),
        ("cruscotto", "stage", "Stage"),
        ("cruscotto", "training", "Training"),
        ("cruscotto", "productivity", "Produttivita"),
        ("cruscotto", "labor_cost", "Costo Lavoro"),
        ("cruscotto", "hours", "Ore"),
        ("cruscotto", "total_hours", "Ore totali"),
        ("cruscotto", "delivery_inc", "Inc"),
        ("cruscotto", "weekday.mon", "Lun"),
        ("cruscotto", "weekday.tue", "Mar"),
        ("cruscotto", "weekday.wed", "Mer"),
        ("cruscotto", "weekday.thu", "Gio"),
        ("cruscotto", "weekday.fri", "Ven"),
        ("cruscotto", "weekday.sat", "Sab"),
        ("cruscotto", "weekday.sun", "Dom"),
        ("cruscotto", "month_revenues", "Revenues (mese)"),
        ("cruscotto", "week_revenues", "Revenues (settimana)"),
        ("cruscotto", "month_receipt", "Receipt (mese)"),
        ("cruscotto", "week_receipt", "Receipt (settimana)"),
        ("cruscotto", "kpi_weekly.title", "Cruscotto - Analisi KPI Settimanale"),
        ("cruscotto", "kpi_weekly.heading", "Analisi KPI Settimanale"),
        ("cruscotto", "kpi_weekly.store", "Store:"),
        ("cruscotto", "kpi_weekly.select_year", "Seleziona anno"),
        ("cruscotto", "kpi_weekly.select_week", "Seleziona settimana"),
        ("cruscotto", "kpi_weekly.week", "Settimana"),
        ("cruscotto", "kpi_weekly.today", "Oggi"),
        ("cruscotto", "kpi_weekly.revenue_compare", "Confronto revenues"),
        ("cruscotto", "kpi_weekly.revenue_compare_help", "Week / Week-1 / MTD (con scontrini)"),
        ("cruscotto", "kpi_weekly.refunds_aggregate", "Rimborsi (aggregato)"),
        ("cruscotto", "kpi_weekly.refunds_help", "Valore rimborsi vs annullati"),
        ("cruscotto", "kpi_weekly.labor_cost", "Costo del lavoro"),
        ("cruscotto", "kpi_weekly.labor_help", "Week - Week -1 - Progressivo mese"),
        ("cruscotto", "kpi_weekly.weekly_report", "Relazione settimanale"),
        ("cruscotto", "kpi_weekly.comments_notes", "Commenti e note"),
        ("cruscotto", "kpi_weekly.note_help", "Digita @ per inserire i KPI della pagina nella relazione."),
        ("cruscotto", "kpi_weekly.reload", "Ricarica"),
        ("cruscotto", "kpi_weekly.save", "Salva"),
        ("cruscotto", "kpi_weekly.note_placeholder", "Scrivi qui la relazione della settimana..."),
        ("cruscotto", "kpi_weekly.week_short", "Week"),
        ("cruscotto", "kpi_weekly.week_minus_1", "Week -1"),
        ("cruscotto", "kpi_weekly.mtd", "MTD"),
        ("cruscotto", "kpi_weekly.data_week", "Dati week"),
        ("cruscotto", "kpi_weekly.variances_week", "Scostamenti week"),
        ("cruscotto", "kpi_weekly.variances_week_minus_1", "Scostamenti week -1"),
        ("cruscotto", "kpi_weekly.month_to_date", "Progressivo mese"),
        ("cruscotto", "kpi_weekly.variances", "Scostamenti"),
        ("cruscotto", "kpi_weekly.vs_budget", "vs Budget"),
        ("cruscotto", "kpi_weekly.vs_last_year", "vs Last Year"),
        ("cruscotto", "kpi_weekly.coverage_data_week", "Copertura dati settimana"),
        ("cruscotto", "kpi_weekly.missing", "Mancanti"),
        ("cruscotto", "kpi_weekly.days_short", "gg"),
        ("cruscotto", "kpi_weekly.delivery_total", "Totale delivery"),
        ("cruscotto", "kpi_weekly.delivery_incidence", "Incidenza delivery"),
        ("cruscotto", "kpi_weekly.delivery_orders", "Ordini delivery"),
        ("cruscotto", "kpi_weekly.cancelled_orders", "Ordini cancellati"),
        ("cruscotto", "kpi_weekly.estimated_opening_pct", "% Apertura delivery stimata"),
        ("cruscotto", "kpi_weekly.estimated_lost_sales", "Vendite perse stimate"),
        ("cruscotto", "kpi_weekly.refunds_pct_orders", "% Rimborsi su ordini"),
        ("cruscotto", "kpi_weekly.refunds_net_value", "Valore rimborsi netto contestazioni accettate"),
        ("cruscotto", "kpi_weekly.delivery_orders_receipts", "Ordini delivery su scontrini"),
        ("cruscotto", "kpi_weekly.avg_receipt", "Scontrino medio"),
        ("cruscotto", "kpi_weekly.calculation_coverage", "Copertura calcolo"),
        ("cruscotto", "kpi_weekly.estimated_potential", "Potenziale stimato"),
        ("cruscotto", "kpi_weekly.opening_provider_config", "Apertura/chiusura da configurazione provider"),
        ("cruscotto", "kpi_weekly.net_disputes", "Netto contestazioni"),
        ("cruscotto", "kpi_weekly.cancelled", "Annullati"),
        ("cruscotto", "kpi_weekly.incidence", "Incidenza"),
        ("cruscotto", "kpi_weekly.cancelled_short", "Cancellati"),
        ("cruscotto", "kpi_weekly.opening", "Apertura"),
        ("cruscotto", "kpi_weekly.reversed_closure", "Chiusura ribaltata"),
        ("cruscotto", "kpi_weekly.lost_estimated_short", "Perse stimate"),
        ("cruscotto", "kpi_weekly.potential", "Potenziale"),
        ("cruscotto", "kpi_weekly.receipt_short", "Scontrino"),
        ("cruscotto", "kpi_weekly.refunds_value", "Valore rimborsi"),
        ("cruscotto", "kpi_weekly.cancelled_refunds", "Rimborsi annullati"),
        ("cruscotto", "kpi_weekly.net_refunds", "Rimborsi netti"),
        ("cruscotto", "kpi_weekly.refunds_value_incidence", "Incidenza valore rimborsi su totale delivery"),
        ("cruscotto", "kpi_weekly.estimated_opening_pct_short", "% apertura stimata"),
        ("cruscotto", "kpi_weekly.coverage", "copertura"),
        ("cruscotto", "kpi_weekly.pct_on_revenues", "% su revenues"),
        ("cruscotto", "kpi_weekly.until_week_minus_1", "MTD fino a week -1"),
        ("cruscotto", "kpi_weekly.last_save", "Ultimo salvataggio"),
        ("cruscotto", "kpi_weekly.no_weekly_report", "Nessuna relazione salvata per questa settimana."),
        ("cruscotto", "kpi_weekly.report_load_error", "Errore caricamento relazione"),
        ("common", "saving", "Salvataggio..."),
        ("common", "save_error", "Errore salvataggio."),
        ("cruscotto", "kpi_weekly.report_saved", "Relazione salvata."),
        ("estrazioni", "toolbar.title", "Estrazioni"),
        ("estrazioni", "toolbar.delivery_data", "Dati Delivery"),
        ("estrazioni", "toolbar.warehouse_data", "Dati Magazzino"),
    ]
    for namespace, key, source in rows:
        seed_translation_key(namespace, key, source)


def seed_pilot_translations() -> None:
    seed_base_translations()
    backfill_supported_languages(include_platform=True, include_tenant=False)


def _fetch_rows(conn, namespace: str = "", language_code: str = "", *, only_customized: bool = False) -> List[Dict[str, Any]]:
    params: list[Any] = []
    where: list[str] = []
    ns = str(namespace or "").strip()
    lang = str(language_code or "").strip().lower()
    if ns:
        where.append("namespace = ?")
        params.append(ns)
    if lang:
        where.append("language_code = ?")
        params.append(_lang(lang))
    if only_customized:
        where.append("customized = 1")
    sql = """
SELECT row_uuid, namespace, translation_key, language_code, source_text,
       text_value, auto_translated, customized, updated_at
FROM dbo.StoreHubTranslations
"""
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY namespace, translation_key, language_code"
    cur = conn.cursor()
    cur.execute(sql, params)
    out = []
    for r in cur.fetchall() or []:
        out.append(
            {
                "row_uuid": str(r[0]),
                "namespace": str(r[1] or ""),
                "translation_key": str(r[2] or ""),
                "language_code": str(r[3] or ""),
                "source_text": str(r[4] or ""),
                "text_value": str(r[5] or ""),
                "auto_translated": bool(r[6]),
                "customized": bool(r[7]),
                "updated_at": r[8],
            }
        )
    return out


def list_translations(namespace: str = "", language_code: str = "") -> List[Dict[str, Any]]:
    return list_effective_translations(namespace=namespace, language_code=language_code)


def _translation_area_label(namespace: str, translation_key: str) -> str:
    ns = str(namespace or "").strip()
    key = str(translation_key or "").strip()
    if ns == "navigation":
        if key.startswith("master."):
            return "Master"
        if key.startswith("admin."):
            return "Admin"
        return "Menu"
    if ns == "common":
        return "Comune"
    if ns == "cash_statement":
        if key.startswith("toolbar."):
            return "Rendiconto - menu"
        return "Rendiconto - distinta cassa"
    if ns == "rendiconto":
        if key.startswith("expenses."):
            return "Rendiconto - spese"
        if key.startswith("deposits."):
            return "Rendiconto - versamenti"
        if key.startswith("delivery."):
            return "Rendiconto - gestione delivery"
        if key.startswith("search."):
            return "Rendiconto - ricerca"
        return "Rendiconto"
    if ns == "warehouse":
        return "Magazzino"
    if ns == "dashboard":
        return "Dashboard"
    if ns == "cruscotto":
        return "Cruscotto"
    if ns == "links":
        return "Link"
    if ns == "orari":
        return "Gestione orari"
    if ns == "estrazioni":
        return "Estrazioni"
    return ns or "Altro"


def _template_key(namespace: str, translation_key: str) -> str:
    return _translation_area_label(namespace, translation_key).lower().replace(" ", "_")


def list_translation_templates(*, include_platform: bool = True, include_tenant_custom: bool = True) -> List[Dict[str, str]]:
    rows: list[dict[str, Any]] = []
    if include_platform:
        ensure_platform_translations_schema()
        conn = _connect_platform(read_only=True)
        try:
            rows.extend(_fetch_rows(conn))
        finally:
            try:
                conn.close()
            except Exception:
                pass
    if include_tenant_custom:
        try:
            ensure_translations_schema()
            conn = get_connection(None, read_only=True)
            try:
                rows.extend(_fetch_rows(conn, only_customized=True))
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
        except Exception:
            pass
    seen: dict[str, str] = {}
    for r in rows:
        label = _translation_area_label(str(r.get("namespace") or ""), str(r.get("translation_key") or ""))
        seen.setdefault(label, _template_key(str(r.get("namespace") or ""), str(r.get("translation_key") or "")))
    out = [{"key": "", "label": "Tutte le aree"}]
    out.extend({"key": key, "label": label} for label, key in sorted(seen.items(), key=lambda x: x[0]))
    return out


def list_base_translation_groups(language_code: str = "it", template: str = "") -> List[Dict[str, Any]]:
    ensure_platform_translations_schema()
    lang = _lang(language_code)
    conn = _connect_platform(read_only=True)
    try:
        rows = _fetch_rows(conn, language_code=lang)
    finally:
        try:
            conn.close()
        except Exception:
            pass
    selected_template = str(template or "").strip()
    buckets: dict[str, dict[str, Any]] = {}
    for r in rows:
        ns = str(r.get("namespace") or "")
        key = str(r.get("translation_key") or "")
        area = _translation_area_label(ns, key)
        area_key = _template_key(ns, key)
        if selected_template and area_key != selected_template:
            continue
        source = str(r.get("source_text") or r.get("text_value") or "").strip()
        group_key = f"{lang}\u241f{source or str(r.get('text_value') or '')}"
        if group_key not in buckets:
            buckets[group_key] = {
                "group_key": group_key,
                "language_code": lang,
                "source_text": source,
                "text_value": str(r.get("text_value") or ""),
                "auto_translated": bool(r.get("auto_translated")),
                "occurrences": [],
                "areas": [],
            }
        bucket = buckets[group_key]
        bucket["occurrences"].append(
            {
                "namespace": ns,
                "translation_key": key,
                "area": area,
                "label": f"{area} / {ns}.{key}",
            }
        )
        if area not in bucket["areas"]:
            bucket["areas"].append(area)
    out = list(buckets.values())
    out.sort(key=lambda g: (str((g.get("areas") or [""])[0]), str(g.get("source_text") or "")))
    return out


def list_tenant_translation_overrides(language_code: str = "it", template: str = "") -> List[Dict[str, Any]]:
    ensure_platform_translations_schema()
    ensure_translations_schema()
    lang = _lang(language_code)
    current_db = str(get_storehub_database_name() or "").strip().lower()
    platform_db = str(_platform_database_name() or "").strip().lower()
    if current_db == platform_db:
        return []
    platform_conn = _connect_platform(read_only=True)
    tenant_conn = get_connection(None, read_only=True)
    try:
        base_rows = _fetch_rows(platform_conn, language_code=lang)
        override_rows = _fetch_rows(tenant_conn, language_code=lang, only_customized=True)
    finally:
        try:
            platform_conn.close()
        except Exception:
            pass
        try:
            tenant_conn.close()
        except Exception:
            pass
    base_by_key = {(r["namespace"], r["translation_key"], r["language_code"]): r for r in base_rows}
    selected_template = str(template or "").strip()
    out = []
    for r in override_rows:
        ns = str(r.get("namespace") or "")
        key = str(r.get("translation_key") or "")
        area = _translation_area_label(ns, key)
        area_key = _template_key(ns, key)
        if selected_template and area_key != selected_template:
            continue
        base = base_by_key.get((ns, key, lang), {})
        row = dict(r)
        row["area"] = area
        row["area_key"] = area_key
        row["base_text_value"] = str(base.get("text_value") or "")
        row["has_base"] = bool(base)
        out.append(row)
    out.sort(key=lambda r: (str(r.get("area") or ""), str(r.get("namespace") or ""), str(r.get("translation_key") or "")))
    return out


def update_base_translation_keys(occurrences: List[Dict[str, str]], language_code: str, text_value: str) -> int:
    ensure_platform_translations_schema()
    lang = _lang(language_code)
    conn = _connect_platform()
    changed = 0
    try:
        for item in occurrences or []:
            ns = str((item or {}).get("namespace") or "").strip()
            key = str((item or {}).get("translation_key") or "").strip()
            if not ns or not key:
                continue
            cur = conn.cursor()
            cur.execute(
                """
SELECT source_text
FROM dbo.StoreHubTranslations
WHERE namespace = ? AND translation_key = ? AND language_code = ?
""",
                (ns, key, lang),
            )
            row = cur.fetchone()
            source = str((row or [""])[0] or text_value or "").strip()
            _upsert_translation(conn, ns, key, lang, source, str(text_value or ""), auto=False, customized=False)
            changed += 1
        conn.commit()
        invalidate_translation_cache()
        return changed
    finally:
        try:
            conn.close()
        except Exception:
            pass


def upsert_tenant_translation_keys(occurrences: List[Dict[str, str]], language_code: str, text_value: str) -> int:
    ensure_translations_schema()
    lang = _lang(language_code)
    conn = get_connection(None)
    changed = 0
    try:
        base_rows = list_effective_translations(language_code=lang)
        base_by_key = {(r["namespace"], r["translation_key"], r["language_code"]): r for r in base_rows}
        for item in occurrences or []:
            ns = str((item or {}).get("namespace") or "").strip()
            key = str((item or {}).get("translation_key") or "").strip()
            if not ns or not key:
                continue
            base = base_by_key.get((ns, key, lang), {})
            source = str(base.get("source_text") or base.get("base_text_value") or text_value or "").strip()
            _upsert_translation(conn, ns, key, lang, source, str(text_value or ""), auto=False, customized=True)
            changed += 1
        conn.commit()
        invalidate_translation_cache()
        return changed
    finally:
        try:
            conn.close()
        except Exception:
            pass


def list_effective_translations(namespace: str = "", language_code: str = "") -> List[Dict[str, Any]]:
    ensure_platform_translations_schema()
    ensure_translations_schema()

    current_db = str(get_storehub_database_name() or "").strip().lower()
    platform_db = str(_platform_database_name() or "").strip().lower()
    platform_conn = _connect_platform(read_only=True)
    tenant_conn = get_connection(None, read_only=True)
    try:
        base_rows = _fetch_rows(platform_conn, namespace=namespace, language_code=language_code)
        override_rows = [] if current_db == platform_db else _fetch_rows(tenant_conn, namespace=namespace, language_code=language_code, only_customized=True)
    finally:
        try:
            platform_conn.close()
        except Exception:
            pass
        try:
            tenant_conn.close()
        except Exception:
            pass

    overrides = {
        (r["namespace"], r["translation_key"], r["language_code"]): r
        for r in override_rows
    }
    rows = []
    seen_keys = set()
    for base in base_rows:
        row = dict(base)
        row_key = (base["namespace"], base["translation_key"], base["language_code"])
        seen_keys.add(row_key)
        override = overrides.get(row_key)
        row["base_text_value"] = base["text_value"]
        row["tenant_text_value"] = (override or {}).get("text_value", "")
        row["text_value"] = row["tenant_text_value"] or row["base_text_value"]
        row["customized"] = bool(override)
        row["has_base"] = True
        row["auto_translated"] = bool(base.get("auto_translated")) and not bool(override)
        rows.append(row)
    for key, override in overrides.items():
        if key in seen_keys:
            continue
        row = dict(override)
        row["base_text_value"] = ""
        row["tenant_text_value"] = override.get("text_value", "")
        row["text_value"] = override.get("text_value", "")
        row["customized"] = True
        row["has_base"] = False
        row["auto_translated"] = bool(override.get("auto_translated"))
        rows.append(row)
    rows.sort(key=lambda r: (str(r.get("namespace") or ""), str(r.get("translation_key") or ""), str(r.get("language_code") or "")))
    return rows


def update_translation_key(namespace: str, translation_key: str, language_code: str, text_value: str) -> bool:
    ensure_translations_schema()
    ns = str(namespace or "common").strip()
    key = str(translation_key or "").strip()
    lang = _lang(language_code)
    if not ns or not key:
        return False

    base_rows = list_effective_translations(namespace=ns, language_code=lang)
    base = next((r for r in base_rows if r["translation_key"] == key), None)
    source = str((base or {}).get("source_text") or text_value or "").strip()
    current_db = str(get_storehub_database_name() or "").strip().lower()
    platform_db = str(_platform_database_name() or "").strip().lower()
    is_platform_base = current_db == platform_db
    conn = _connect_platform() if is_platform_base else get_connection(None)
    try:
        _upsert_translation(conn, ns, key, lang, source, str(text_value or ""), auto=False, customized=not is_platform_base)
        conn.commit()
        invalidate_translation_cache()
        return True
    finally:
        try:
            conn.close()
        except Exception:
            pass


def update_translation(row_uuid: str, text_value: str) -> bool:
    ensure_translations_schema()
    rid = str(row_uuid or "").strip()
    if not rid:
        return False
    conn = get_connection(None)
    try:
        cur = conn.cursor()
        cur.execute(
            """
UPDATE dbo.StoreHubTranslations
SET text_value = ?, customized = 1, auto_translated = 0, updated_at = SYSUTCDATETIME()
WHERE row_uuid = ?
""",
            (str(text_value or ""), rid),
        )
        changed = int(cur.rowcount or 0)
        conn.commit()
        invalidate_translation_cache()
        return changed > 0
    finally:
        try:
            conn.close()
        except Exception:
            pass


def upsert_tenant_custom_translation(
    namespace: str,
    translation_key: str,
    source_text: str,
    values: Dict[str, str] | None = None,
) -> None:
    ensure_translations_schema()
    ns = str(namespace or "custom").strip()
    key = str(translation_key or "").strip()
    source = str(source_text or "").strip()
    if not ns or not key or not source:
        return
    values = values or {}
    conn = get_connection(None)
    try:
        cur = conn.cursor()
        for lang in SUPPORTED_LANGUAGES:
            code = lang["code"]
            cur.execute(
                """
SELECT text_value, auto_translated, customized
FROM dbo.StoreHubTranslations
WHERE namespace = ? AND translation_key = ? AND language_code = ?
""",
                (ns, key, code),
            )
            existing = cur.fetchone()
            if existing:
                existing_text = str(existing[0] or "").strip()
                existing_auto = bool(existing[1])
                existing_custom = bool(existing[2])
                explicit_value = str(values.get(code) or "").strip()
                if existing_custom and not existing_auto and not explicit_value and existing_text != source:
                    continue
            value = str(values.get(code) or "").strip() or (source if code == "it" else _auto_translate(source, code))
            _upsert_translation(conn, ns, key, code, source, value, auto=(code != "it" and not values.get(code)), customized=True)
        conn.commit()
        invalidate_translation_cache()
    finally:
        try:
            conn.close()
        except Exception:
            pass


def reset_translation_key(namespace: str, translation_key: str, language_code: str) -> bool:
    ensure_translations_schema()
    current_db = str(get_storehub_database_name() or "").strip().lower()
    platform_db = str(_platform_database_name() or "").strip().lower()
    if current_db == platform_db:
        return False
    conn = get_connection(None)
    try:
        cur = conn.cursor()
        cur.execute(
            """
DELETE FROM dbo.StoreHubTranslations
WHERE namespace = ? AND translation_key = ? AND language_code = ? AND customized = 1
""",
            (str(namespace or "").strip(), str(translation_key or "").strip(), _lang(language_code)),
        )
        changed = int(cur.rowcount or 0)
        conn.commit()
        invalidate_translation_cache()
        return changed > 0
    finally:
        try:
            conn.close()
        except Exception:
            pass


def translation_map(language_code: str = "it") -> Dict[str, str]:
    lang = _lang(language_code)
    try:
        tenant_db = str(get_storehub_database_name() or "").strip().lower()
    except Exception:
        tenant_db = ""
    cache_key = (tenant_db, lang)
    now = time.time()
    with _MAP_CACHE_LOCK:
        entry = _MAP_CACHE.get(cache_key)
    if entry and (now - entry[0]) < _MAP_CACHE_TTL_SECONDS:
        return entry[1]
    rows = list_effective_translations(language_code=lang)
    out: Dict[str, str] = {}
    for r in rows:
        ns = r["namespace"]
        key = r["translation_key"]
        value = str(r["text_value"] or "")
        source = str(r.get("source_text") or "")
        if lang != "it" and value.strip() == source.strip():
            value = _auto_translate(source, lang)
        out[key] = value
        out[_full_key(ns, key)] = value
    with _MAP_CACHE_LOCK:
        _MAP_CACHE[cache_key] = (now, out)
    return out


def translate_text(namespace: str, translation_key: str, source_text: str, language_code: str = "it") -> str:
    ns = str(namespace or "common").strip()
    key = str(translation_key or "").strip()
    source = str(source_text or "").strip()
    if not key:
        return source
    lang = _lang(language_code)
    try:
        from flask import g, has_request_context

        if has_request_context():
            cache_key = f"_i18n_{lang}"
            cache = getattr(g, cache_key, None)
            if cache is None:
                cache = translation_map(lang)
                setattr(g, cache_key, cache)
            value = cache.get(key) or cache.get(_full_key(ns, key))
            if value:
                return value
            if lang != "it":
                it_cache = getattr(g, "_i18n_it", None)
                if it_cache is None:
                    it_cache = translation_map("it")
                    setattr(g, "_i18n_it", it_cache)
                value = it_cache.get(key) or it_cache.get(_full_key(ns, key))
                if value:
                    return value
    except Exception:
        pass
    return _auto_translate(source, lang) or source
