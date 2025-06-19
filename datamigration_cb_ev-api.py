import re
import time

import pandas as pd
import numpy as np

from easyverein import EasyvereinAPI
from easyverein.models.contact_details import ContactDetailsFilter, ContactDetails
from easyverein.models.invoice import InvoiceCreate, InvoiceFilter
from easyverein.models.invoice_item import InvoiceItem
import datetime as dt
from easyverein.core.exceptions import EasyvereinAPIException

# HIER API-KEY EINTRAGEN
api_key = ''

ev_client = EasyvereinAPI(
    api_key,
    api_version="v2.0",
    base_url="https://hexa.easyverein.com/api/",
    logger=None,
)

# TODO bis 14.09 müssten die Gastspieler Daten in CB jetzt soweit passen
# TODO: Wenn Gast (bzw. nicht Mitglied) immer Rechnung
# TODO Erstellung Gastspieler mit IBAN Feldern (sepaMandate = Mandatsreferenz "EV...")?

dryrun = True


def create_guestplayer(contact,
                       contactDetailsGroup_Guest=['https://easyverein.com/api/v1.7/contact-details-group/187854580'],
                       dryrun=False):
    """
    Nimmt Informationen aus Courtbooking und baut ein contact-obj daraus. Bei bestimmten Feldern wird im Falle des nicht
    Vorhandenseins (np.nan) der Wert auf "" gesetzt, da immer noch eine Rechnungserstellung mittels E-Mail möglich ist

    :param contact: contact_obj
    :param contactDetailsGroup_Guest: Definition für Gastgruppe in eV
    :param dryrun: true or false
    :return:
    """
    keys_to_check = ["Ort", "Straße", "Handynummer", "Telefonnummer", "IBAN", "BIC", "Mandatsreferenz", "plz"]
    contact = {k: ("" if v is np.nan and k in keys_to_check else v) for k, v in contact.items()}

    guest_player = ContactDetails(firstName=contact["Vorname"], familyName=contact["Nachname"], isCompany=False,
                                  primaryEmail=contact['E-Mail'],
                                  privateEmail=contact['E-Mail'],
                                  salutation=contact['Anrede'],
                                  street=contact["Straße"],
                                  city=contact["Ort"],
                                  zip=contact["plz"],
                                  mobilePhone=contact["Handynummer"],
                                  privatePhone=contact["Telefonnummer"],
                                  methodOfPayment=methodOfPayment(zahlungsart=contact["Zahlungsart"]),
                                  iban=contact["IBAN"],
                                  bic=contact["BIC"],
                                  sepaMandate=contact["Mandatsreferenz"],
                                  preferredEmailField=1,
                                  preferredCommunicationWay=0
                                  )
    guest_player.contactDetailsGroups = contactDetailsGroup_Guest
    if not dryrun:
        output = ev_client.contact_details.create(
            guest_player)
        contact["contact_obj"] = output
    else:
        output = "Dryrun"
    return contact


def create_invoice(contact, completion_date, dryrun=False, account='Hauptkonto'):
    current_year = dt.datetime.now().year
    current_invoice_nr = get_current_invoice_nr(current_year=current_year)

    invoice = InvoiceCreate(
        invNumber=create_invoice_id(current_year=current_year, current_invoice_nr=current_invoice_nr),
        # invNumber zB 2024-631
        totalPrice=calculate_preis(preisliste=contact["_Preis"], art='Getränk'),
        relatedAddress=contact["contact_obj"],
        paymentInformation=paymentInformation(contact["contact_obj"].methodOfPayment))
    invoice.kind = "revenue"  # Einnahme des Vereins
    invoice.receiver = create_receiver_string(
        contact_obj=contact["contact_obj"])
    if account == 'Hauptkonto':
        invoice.selectionAcc = 187408412
    invoice.relatedAddress = 'https://easyverein.com/api/v1.7/contact-details/' + str(contact["contact_obj"].id)

    items = create_invoice_items(contact=contact, art='Getränk', completion_date=completion_date)

    if not dryrun:
        output = ev_client.invoice.create_with_items(invoice, items=items)
    else:
        output = "Dryrun"
    return output


def create_invoice_items(contact, art, completion_date):
    items = []
    if art == 'Gast':
        data = {
            'Vorname': contact["Vorname"],
            'Nachname': contact["Nachname"],
            '_Preis': contact["_Preis"],
            'Buchungszeit': contact["Buchungszeit"],
            'Dauer': contact["Dauer"]
        }
        for i in range(len(data['_Preis'])):
            buchungstext = "Gastspielerposten am %(date)s, Dauer: %(duration)s" % {"date": data['Buchungszeit'][i],
                                                                                   "duration": data['Dauer'][i]}
            invoice_item = InvoiceItem(title=buchungstext, quantity=1, unitPrice=data['_Preis'][i],
                                       description='Informationen Gästenutzung: https://www.tc-grafrath.de/der-verein/gaeste.html',
                                       taxRate=0.00, taxName=' ')
            invoice_item.billingAccount = 'https://easyverein.com/api/v2.0/billing-account/44093'
            items.append(invoice_item)

    elif art == 'Getränk':
        data = {
            'Vorname': contact["Vorname"],
            'Nachname': contact["Nachname"],
            '_Preis': contact["_Preis"],
            'Kaufdatum': contact["_Kaufdatum"],
            'Anzahl': contact["Anzahl"],
            'Getränk': [list(set(i)) for i in contact['Getränk']] # damit im Posten das Getränk nur einmal steht
        }
        for i in range(len(data['_Preis'])):
            buchungstext = "Getränkebuchung am %(date)s, Anzahl Getränke: %(Anzahl)s aus Listenposten: %(Posten)s" % {
                "date": data['Kaufdatum'][i].strftime(format='%d.%m.%Y'),
                "Anzahl": sum(data['Anzahl'][i]),
                "Posten": ', '.join(data['Getränk'][i])}
            invoice_item = InvoiceItem(title=buchungstext, quantity=1, unitPrice=sum(data['_Preis'][i]),
                                       description=get_description(kaufdatum=contact["_Kaufdatum"][i],
                                                                   completion_date=completion_date),
                                       taxRate=0.00, taxName=' ')
            invoice_item.billingAccount = 'https://easyverein.com/api/v2.0/billing-account/44134'
            items.append(invoice_item)
    return items


def get_description(kaufdatum, completion_date):
    if kaufdatum == completion_date:
        return 'Abrechnung laut ausliegender Getränkeliste - Buchungstag als Abrechnungstag gesetzt'
    else:
        return 'Preise siehe Preisliste Courtbooking'


def calculate_preis(preisliste, art):
    if art == 'Gast':
        return sum(preisliste)
    elif art == "Getränk":
        return sum([sum(i) for i in preisliste])


def get_current_invoice_nr(current_year):
    first_day_of_year = dt.datetime(current_year, 1, 1).date().strftime("%Y-%m-%d")
    search = InvoiceFilter(
        date__gt=first_day_of_year
    )
    all_invoices = ev_client.invoice.get_all(search=search, limit_per_page=1000)
    pattern = r'^\d{4}-(\d{3,5})'
    current_invoice_nr = 0
    for inv in all_invoices:
        if isinstance(inv.invNumber, str):
            match = re.match(pattern, inv.invNumber)
            if match:
                invoice_nr = int(match.group(1))
                if invoice_nr > current_invoice_nr:
                    current_invoice_nr = invoice_nr
    return current_invoice_nr


def create_invoice_id(current_year, current_invoice_nr) -> str:
    """
    create incoice-id out of players information: lastName_firstName_DateOfFirstBooking
    :param contact: contact-dict
    :return: invoice_id
    """
    invoice_id = "-".join([str(current_year), str(current_invoice_nr + 1)])
    return invoice_id


def create_receiver_string(contact_obj) -> str:
    """
    Anschrift der Rechnung auf Rechnungs-pdf
    :param contact_obj: contact_obj
    :return: Anschrift als string
    """
    return '%(Anrede)s %(first_name)s %(last_name)s\r\n%(street)s\r\n%(zip)s %(city)s' % {
        "Anrede": contact_obj.salutation,
        "first_name": contact_obj.firstName,
        "last_name": contact_obj.familyName,
        "street": contact_obj.street,
        "zip": contact_obj.zip,
        "city": contact_obj.city}


def methodOfPayment(zahlungsart) -> int:
    """
    methodOfPayment:
    Defines the method of payment preferred by the user. Converts it from Courtbooking entry (Rechnung / Lastschrift)
    to easyVerein entry 1,2,4

    Possible values:

    - 0: not selected
    - 1: direct debit
    - 2: bank transfer
    - 3: cash
    - 4: other
    :param contact:
    :return:
    """

    return 2  # TODO provisorisch immer "Rechnung" für Gastspieler, später siehe auskommentierter Titel unten

    # if zahlungsart == "Lastschrift":
    #     return 1
    # elif zahlungsart == "Rechnung":
    #     return 2
    # else:
    #     return 4


def paymentInformation(methodOfPayment) -> str:
    """
    methodOfPayment:
    Defines the method of payment for the invoice:

    Possible values:

    - 0: not selected
    - 1: debit (Lastschrift)
    - 2: account (Rechnung)
    - 3: cash
    - 4: other
    :param contact:
    :return:
    """
    if methodOfPayment == 1:
        return "debit"
    elif methodOfPayment == 2:
        return "account"
    else:
        return "other"


def clean_buchungen(df) -> pd.DataFrame:
    """
    data-cleaning of csv from Courtbooking
    :param df: DataFrame of buchungs-csv from Courtbooking
    :return: cleaned DataFrame
    """
    doc_list = []
    for doc in df.to_dict(orient='records'):
        doc["Buchungszeit"] = doc["_Datum"].date().strftime(format='%d.%m.%Y') + " " + doc["_Von"].isoformat()
        doc["Spieler_cleaned"] = doc["Spieler"].replace("  ", " ").replace("; ", ";").replace(" ;", ";")
        doc["Zahler"] = doc["Spieler_cleaned"].split(";")[0]
        match = re.match(r"^(\S+)\s+(.+)$", doc["Zahler"])
        doc["Vorname"] = match.groups()[0].replace(' ', '')
        doc["Vorname"] = match.groups()[0].replace('- ', '-')
        doc["Nachname"] = match.groups()[1].replace(' ', '')
        doc["Nachname"] = match.groups()[1].replace('- ', '-')
        doc["Nichtzahler"] = doc["Spieler_cleaned"].split(";")[1]
        doc_list.append(doc)
    df_cleaned_buchungen = pd.DataFrame(doc_list)
    return df_cleaned_buchungen


def get_members_in_ev() -> pd.DataFrame:
    """
    Request and transformation of all contacts from easyVerein to pandas DataFrame
    :return: DataFrame with all contacts
    """
    search = ContactDetailsFilter(
        contactDetailsGroups__not="193181175"
        # alle Nicht-Firmen filtern, alle Member und ehemaligen member mit member: None
    )
    all_contacts = ev_client.contact_details.get_all(search=search)
    all_members = []
    for mem in all_contacts:
        doc = {}
        doc["Vorname"] = mem.firstName
        doc["iban"] = mem.iban
        doc["Nachname"] = mem.familyName
        doc["email"] = mem.primaryEmail
        doc["strasse"] = mem.street
        doc["stadt"] = mem.city
        doc["plz"] = str(mem.zip)
        doc["stadt"] = mem.city
        if mem.contactDetailsGroups == ['https://easyverein.com/api/v2.0/contact-details-group/187854580']:
            doc["Gruppe"] = "Gast"
            doc["contact_obj"] = mem
        elif mem.contactDetailsGroups == ['https://easyverein.com/api/v2.0/contact-details-group/193181080']:
            doc["Gruppe"] = "Mitglied"
            doc["contact_obj"] = mem
        else:
            print("ACHTUNG UNBEKANNTE KONTAKTGRUPPE! - Zeile 215 Skript! - %(mem)s" % {"mem": mem})
        all_members.append(doc)
    df_all_members = pd.DataFrame(all_members)
    return df_all_members


def doublecheck_billing(df_not_paid, df_alltime):
    """
    takes dataframe with current open bills and dataframe with iterative bills from past and returns DataFrame,
    and return dataframe which just contains bill, not paid and accounted in past.
    """
    for col in df_not_paid.columns:
        if df_not_paid[col].dtype != df_alltime[col].dtype:
            df_not_paid[col] = df_not_paid[col].astype(str)
            df_alltime[col] = df_alltime[col].astype(str)
    df_still_to_pay = df_not_paid.merge(df_alltime, how='outer', indicator=True).query('_merge == "left_only"').drop(
        columns=['_merge'])
    return df_still_to_pay


def save_billing_to_alltime(firstName, lastName, df_cleaned_buchungen, csv_buchungen_alltime):
    df_current_getränke = df_cleaned_buchungen[
        (df_cleaned_buchungen["Vorname"] == firstName) & (df_cleaned_buchungen["Nachname"] == lastName)]
    df_alltime_current = pd.read_csv(csv_buchungen_alltime, encoding='latin1', sep=';')
    df_combined = pd.concat([df_alltime_current, df_current_getränke], ignore_index=False)
    df_combined.to_csv(csv_buchungen_alltime, sep=";", encoding='latin1', index=False)


def main(csv_file_path, filename_buchungen, filename_mitglieder, buchungen_alltime, completion_date, dryrun=False):
    """
    main-function of script
    :param csv_file_path: Path to the downloaded csvs from Courtbooking
    :param filename_buchungen: filename of the bookings-csv from Courtbooking
    :param filename_mitglieder: filename of the members-list-csv from Courtbooking
    :param dryrun: defines whether writing operations to easyVerein are executed
    :return:
    """
    csv_buchungen = csv_file_path + filename_buchungen
    csv_buchungen_alltime = csv_file_path + buchungen_alltime
    csv_mitglieder = csv_file_path + filename_mitglieder

    # Einlesen der CSV-Datei mit dem angegebenen Encoding
    df_todo_raw = pd.read_csv(csv_buchungen, encoding='latin1', sep=';')
    df_todo = df_todo_raw.copy()
    df_todo['_Kaufdatum'] = pd.to_datetime(df_todo['Kaufdatum'], format='%d.%m.%Y %H:%M').dt.date

    df_alltime = pd.read_csv(csv_buchungen_alltime, encoding='latin1',
                             sep=';')  # CSV aller in der Vergangenehit abgerechneten Buchungen
    df_alltime['_Kaufdatum'] = pd.to_datetime(df_alltime['Kaufdatum'], format='%d.%m.%Y %H:%M').dt.date
    df_not_paid = df_todo.loc[df_todo["Gezahlt"] == 'Nicht gezahlt']

    # das raw CB csv wie es eingelesen wird, wird hier Doppelgecheckt nach bereits abgerechneten Buchungen, diese werden entfernt.
    # Später wird das "alltime"-CSV bereits abgerechneter Buchung direkt nach jeder Rechnungserstellung um die neu abgerehcneten Einträge im Raw Format ergänzt und abgespeichert
    df = doublecheck_billing(df_not_paid=df_not_paid, df_alltime=df_alltime)
    # TODO Am besten hierfür wohl nur auf Vorname, Name, Kaufdatum checken, da sonst zu kompliziert/ granular

    # Umwandeln der Datums- und Zeitspalten
    # df['_Kaufdatum'] = pd.to_datetime(df['Kaufdatum'], format='%d.%m.%Y %H:%M') # Gaeste
    # df['_Von'] = pd.to_datetime(df['Von'], format='%H:%M Uhr').dt.time # Gaeste
    # df['_Bis'] = pd.to_datetime(df['Bis'], format='%H:%M Uhr').dt.time # Gaeste

    # Preis-Spalte in Float umwandeln
    df['_Preis'] = df['Preis'].apply(lambda x: float(x.replace(',', '.')))
    df['Anzahl'] = df['Anzahl'].apply(lambda x: int(x))
    # df_cleaned_buchungen = clean_buchungen(df) # Gaeste

    df_mitgliederliste = pd.read_csv(csv_mitglieder, encoding='latin1', sep=';')
    # df_mitgliederliste['Vorname'] = df_mitgliederliste['Vorname'].str.replace(' ', '') #TODO das macht bei Doppelvornamen Probleme!
    # df_mitgliederliste['Nachname'] = df_mitgliederliste['Nachname'].str.replace(' ', '') #TODO das macht bei Doppelnachnamen Probleme!
    # df_mitgliederliste['Anrede'] = df_mitgliederliste.apply(lambda x: 'Herr' if )

    df.sort_values("_Kaufdatum", ascending=True,
                   inplace=True)  # damit erste Buchung in der folgenden Liste vorne steht

    df_grouped_all = df.groupby(['Vorname', 'Nachname', '_Kaufdatum']).agg({
        'Getränk': list,
        'Anzahl': list,
        '_Preis': list
    }).reset_index()

    df_grouped_all_person = df_grouped_all.groupby(['Vorname', 'Nachname']).agg({
        '_Kaufdatum': list,
        'Getränk': list,
        'Anzahl': list,
        '_Preis': list
    }).reset_index()

    # TODO hier weiter, Datenstruktur ist: Kaufdatum: Liste, Getrönk, Anzahl, Preis: Liste aus Listen, Zeilen werden durch VOrname, Nachname bestimmt

    merged_df = pd.merge(df_grouped_all_person, df_mitgliederliste, on=['Vorname', 'Nachname'], how='left')
    merged_df['Anrede'] = merged_df['Geschlecht'].apply(lambda x: 'Herr' if x == 'Männlich' else 'Frau')
    merged_df['plz'] = merged_df['PLZ'].apply(lambda x: str(x) if np.isnan(x) else str(int(x)))
    merged_df['Telefonnummer'] = merged_df['Telefonnummer'].apply(
        lambda x: str(x).replace(" ", "").replace("/", "") if type(x) == str else None)
    merged_df['Handynummer'] = merged_df['Handynummer'].apply(
        lambda x: str(x).replace(" ", "").replace("/", "") if type(x) == str else None)
    df_all_members = get_members_in_ev()

    merged_all_players_df = pd.merge(merged_df, df_all_members, on=['Vorname', 'Nachname', 'plz'], how='left')

    for contact in merged_all_players_df.to_dict(orient='records'):
        # if not ((contact["Nachname"] == "Dohn" and contact["Vorname"] == "Lukas")):  # TODO ZU DEBUGGING ZWECKEN
        #     continue
        # if not ((contact["Nachname"] == "Fischer" and contact["Vorname"] == "Charlotte") or (
        #         contact["Nachname"] == "Lechner" and contact[
        #     "Vorname"] == "Christian")):  # TODO zum Testen für Dryrun = False
        #     continue
        if contact["Gruppe"] == "Mitglied" or contact["Gruppe"] == "Gast":
            try:
                output = create_invoice(contact=contact,
                                        dryrun=dryrun,
                                        completion_date=completion_date)  # Mitglied oder Gastpieler ist in easyVerein => Erstelung der Rechnung
                if not dryrun:
                    print("created invoice in easyVerein: %(invoice)s" % {"invoice": output})
                    save_billing_to_alltime(firstName=contact["Vorname"], lastName=contact["Nachname"],
                                            # CB raw csv wird ergänzt
                                            df_cleaned_buchungen=df_todo_raw,
                                            csv_buchungen_alltime=csv_buchungen_alltime)
                    print("SAVED BILLINGS FOR PLAYER %(first_name)s %(family_name)s TO ALLTIME TABLE!" % {
                        "first_name": contact["Vorname"],
                        "family_name": contact["Nachname"]})
                    time.sleep(10) # sonst too many requests error
            except KeyError as e:
                print(
                    "ERROR WHILE CREATING INVOICE FOR PLAYER %(first_name)s %(family_name)s: %(error)s - Missing information in easyVerein!" % {
                        "first_name": contact["Vorname"],
                        "family_name": contact["Nachname"],
                        "error": str(e)})
            except ValueError as e:
                print("VALUE ERROR for %(Vorname)s %(Nachname)s: %(error)s" % {"Vorname": contact["Vorname"],
                                                                               "Nachname": contact["Nachname"],
                                                                               "error": e})
            except EasyvereinAPIException as e:
                print(
                    "ERROR WHILE CREATING INVOICE FOR GUESTPLAYER %(first_name)s %(family_name)s: %(error)s - Invoice ID already existing!" % {
                        "first_name": contact["Vorname"],
                        "family_name": contact["Nachname"],
                        "error": str(e)})

        # if pd.isna(contact["Gruppe"]):
        #     raise NotImplementedError
        #     try:
        #         contact = create_guestplayer(contact=contact,
        #                                      dryrun=dryrun)  # Gastspieler anlegen und Rechnung erstellen
        #         if not dryrun:
        #             print("created guestplayer %(first_name)s %(family_name)s in easyVerein!" % {
        #                 "first_name": contact["Vorname"],
        #                 "family_name": contact["Nachname"]})
        #     except Exception as e:
        #         print("ERROR WHILE CREATING GUESTPLAYER %(first_name)s %(family_name)s: %(error)s" % {
        #             "first_name": contact["Vorname"],
        #             "family_name": contact["Nachname"],
        #             "error": str(e)})
        #     try:
        #         if not dryrun:
        #             output_invoice = create_invoice(contact=contact, dryrun=dryrun)
        #             print("created invoice in easyVerein: %(invoice)s" % {"invoice": output_invoice})
        #             save_billing_to_alltime(firstName=contact["Vorname"], lastName=contact["Nachname"],
        #                                     # CB raw csv wird ergänzt
        #                                     df_cleaned_buchungen=df_todo_raw,
        #                                     csv_buchungen_alltime=df_alltime)
        #             print("SAVED BILLINGS FOR PLAYER %(first_name)s %(family_name)s TO ALLTIME TABLE!" % {
        #                 "first_name": contact["Vorname"],
        #                 "family_name": contact["Nachname"]})
        #     except KeyError as e:
        #         print(
        #             "ERROR WHILE CREATING INVOICE FOR GUESTPLAYER %(first_name)s %(family_name)s: %(error)s - Missing information in easyVerein!" % {
        #                 "first_name": contact["Vorname"],
        #                 "family_name": contact["Nachname"],
        #                 "error": str(e)})
        #     except EasyvereinAPIException as e:
        #         print(
        #             "ERROR WHILE CREATING INVOICE FOR GUESTPLAYER %(first_name)s %(family_name)s: %(error)s" % {
        #                 "first_name": contact["Vorname"],
        #                 "family_name": contact["Nachname"],
        #                 "error": str(e)})


if __name__ == '__main__':
    main(csv_file_path='C:/Users/Megaport/Desktop/TCGrafrath/03_Datenstatus_CBvsEasyVerein/Getränke/',
         filename_buchungen='getraenkeliste.csv',
         filename_mitglieder='20240914_mitgliederliste.csv',
         buchungen_alltime='Gesamtübersicht_getraenke.csv',
         dryrun=False,
         completion_date=dt.date(2024, 12, 6)) #TODO completion_date nur für Getränkeabrechnung relevant (Hinweis bei Vereinsgetränkeliste) muss Lösung gefunden werden beim zusammenführen mit Gästebuchungen
