Attribute VB_Name = "ImportOrganicoRAL"
Option Explicit

Private Const TARGET_SHEET As String = "RAL"
Private Const FIRST_DATA_ROW As Long = 2

Public Sub UploadOrganicoRAL()
    Dim cn As Object
    Dim cmd As Object
    Dim ws As Worksheet
    Dim lastRow As Long
    Dim r As Long
    Dim serverName As String
    Dim dbName As String
    Dim userName As String
    Dim userPwd As String
    Dim driverName As String
    Dim connStr As String
    Dim rowsDone As Long

    Set ws = ThisWorkbook.Worksheets(TARGET_SHEET)
    lastRow = ws.Cells(ws.Rows.Count, 1).End(xlUp).Row
    If lastRow < FIRST_DATA_ROW Then
        MsgBox "Nessuna riga da importare nel foglio " & TARGET_SHEET & ".", vbExclamation
        Exit Sub
    End If

    serverName = InputBox("SQL Server\istanza", "Connessione SQL Server", "10.24.1.1\SQLEXPRESS")
    If Len(Trim$(serverName)) = 0 Then Exit Sub

    dbName = InputBox("Database", "Connessione SQL Server", "APP_STOREHUB")
    If Len(Trim$(dbName)) = 0 Then Exit Sub

    userName = InputBox("Utente SQL", "Connessione SQL Server", "file")
    If Len(Trim$(userName)) = 0 Then Exit Sub

    userPwd = InputBox("Password SQL", "Connessione SQL Server")
    driverName = "ODBC Driver 18 for SQL Server"

    connStr = "Driver={" & driverName & "};" & _
              "Server=" & serverName & ";" & _
              "Database=" & dbName & ";" & _
              "Uid=" & userName & ";" & _
              "Pwd=" & userPwd & ";" & _
              "Encrypt=no;" & _
              "TrustServerCertificate=yes;"

    Set cn = CreateObject("ADODB.Connection")
    cn.Open connStr
    cn.CommandTimeout = 120

    Application.ScreenUpdating = False
    Application.EnableEvents = False
    Application.StatusBar = "Import Organico in corso..."

    On Error GoTo CleanFail

    Set cmd = CreateObject("ADODB.Command")
    Set cmd.ActiveConnection = cn
    cmd.CommandType = 4
    cmd.CommandText = "dbo.usp_upsert_organico_ral"

    For r = FIRST_DATA_ROW To lastRow
        If WorksheetFunction.CountA(ws.Rows(r)) = 0 Then GoTo NextRow

        cmd.Parameters.Refresh
        cmd.Parameters("@codice_azienda").Value = NzText(ws.Cells(r, 1).Value)
        cmd.Parameters("@denominazione").Value = NzNullText(ws.Cells(r, 2).Value)
        cmd.Parameters("@data_inizio_periodo").Value = NzDate(ws.Cells(r, 3).Value)
        cmd.Parameters("@data_fine_periodo").Value = NzDate(ws.Cells(r, 4).Value)
        cmd.Parameters("@dipendente").Value = NzText(ws.Cells(r, 5).Value)
        cmd.Parameters("@cognome").Value = NzNullText(ws.Cells(r, 6).Value)
        cmd.Parameters("@nome").Value = NzNullText(ws.Cells(r, 7).Value)
        cmd.Parameters("@codice_fiscale").Value = NzNullText(ws.Cells(r, 8).Value)
        cmd.Parameters("@data_assunzione").Value = NzDateNull(ws.Cells(r, 9).Value)
        cmd.Parameters("@data_cessazione").Value = NzDateNull(ws.Cells(r, 10).Value)
        cmd.Parameters("@filiale").Value = NzNullText(ws.Cells(r, 11).Value)
        cmd.Parameters("@centro_di_costo").Value = NzNullText(ws.Cells(r, 12).Value)
        cmd.Parameters("@importo_elemento_di_paga").Value = NzDecimalNull(ws.Cells(r, 13).Value)
        cmd.Parameters("@ral").Value = NzDecimalNull(ws.Cells(r, 14).Value)
        cmd.Parameters("@cod_contratto").Value = NzNullText(ws.Cells(r, 15).Value)
        cmd.Parameters("@natura_rapporto").Value = NzNullText(ws.Cells(r, 16).Value)
        cmd.Parameters("@percentuale_part_time").Value = NzDecimalNull(ws.Cells(r, 17).Value)
        cmd.Parameters("@codice_presenze").Value = NzNullText(ws.Cells(r, 18).Value)
        cmd.Parameters("@source_file_name").Value = ThisWorkbook.Name
        cmd.Parameters("@source_sheet_name").Value = ws.Name
        cmd.Parameters("@source_row_num").Value = r

        cmd.Execute
        rowsDone = rowsDone + 1
        Application.StatusBar = "Import Organico: riga " & r & " di " & lastRow

NextRow:
    Next r

    MsgBox "Import completato. Righe elaborate: " & rowsDone, vbInformation

CleanExit:
    On Error Resume Next
    Application.StatusBar = False
    Application.ScreenUpdating = True
    Application.EnableEvents = True
    If Not cmd Is Nothing Then Set cmd = Nothing
    If Not cn Is Nothing Then
        If cn.State <> 0 Then cn.Close
        Set cn = Nothing
    End If
    Exit Sub

CleanFail:
    MsgBox "Errore alla riga " & r & ": " & Err.Description, vbCritical
    Resume CleanExit
End Sub

Private Function NzText(ByVal v As Variant) As String
    NzText = Trim$(CStr(v))
End Function

Private Function NzNullText(ByVal v As Variant) As Variant
    Dim s As String
    s = Trim$(CStr(v))
    If Len(s) = 0 Then
        NzNullText = Null
    Else
        NzNullText = s
    End If
End Function

Private Function NzDate(ByVal v As Variant) As Date
    NzDate = CDate(v)
End Function

Private Function NzDateNull(ByVal v As Variant) As Variant
    If IsEmpty(v) Or Len(Trim$(CStr(v))) = 0 Then
        NzDateNull = Null
    Else
        NzDateNull = CDate(v)
    End If
End Function

Private Function NzDecimalNull(ByVal v As Variant) As Variant
    If IsEmpty(v) Or Len(Trim$(CStr(v))) = 0 Then
        NzDecimalNull = Null
    Else
        NzDecimalNull = CDbl(v)
    End If
End Function
