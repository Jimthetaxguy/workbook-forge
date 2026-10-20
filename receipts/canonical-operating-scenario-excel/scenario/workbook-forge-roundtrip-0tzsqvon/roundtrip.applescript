on canonicalPath(pathText)
    if pathText starts with "/" then
        return POSIX path of ((POSIX file pathText) as alias)
    end if
    return POSIX path of (pathText as alias)
end canonicalPath

on run argv
    set workbookPath to item 1 of argv
    set workbookName to item 2 of argv
    set workbookFile to (POSIX file workbookPath) as alias
    with timeout of 180 seconds
        tell application "Microsoft Excel"
            if (count of workbooks) is not 0 then error "Full rebuild requires Excel to have no other open workbooks"
            set modeBefore to calculation as text
            set iterationBefore to iteration as text
            set maxIterationsBefore to max iterations as text
            set maxChangeBefore to max change as text
            
            log "stage:open"
            open workbookFile
            log "stage:identity"
            if not (exists workbook workbookName) then error "SDK workbook did not open"
            set targetBook to workbook workbookName
            if (name of targetBook) is not workbookName then error "SDK workbook identity mismatch"
            set openedPath to my canonicalPath(full name of targetBook)
            if openedPath is not workbookPath then error "SDK workbook path mismatch"
            if (count of workbooks) is not 1 then error "Unexpected workbook opened during isolated round-trip"
            set value of range "B1" of worksheet "Assumptions" of targetBook to 25
            log "stage:full_rebuild"
            calculate full rebuild
            log "stage:save"
            save targetBook
            log "stage:close"
            close targetBook saving no
            set modeAfter to calculation as text
            set iterationAfter to iteration as text
            set maxIterationsAfter to max iterations as text
            set maxChangeAfter to max change as text
            set metadata to "engine=Microsoft Excel Desktop" & linefeed
            set metadata to metadata & "excel_version=" & (version as text) & linefeed
            set metadata to metadata & "calculation_version=" & (calculation version as text) & linefeed
            set metadata to metadata & "calculation_mode_before=" & modeBefore & linefeed
            set metadata to metadata & "calculation_mode_after=" & modeAfter & linefeed
            set metadata to metadata & "iteration_before=" & iterationBefore & linefeed
            set metadata to metadata & "iteration_after=" & iterationAfter & linefeed
            set metadata to metadata & "max_iterations_before=" & maxIterationsBefore & linefeed
            set metadata to metadata & "max_iterations_after=" & maxIterationsAfter & linefeed
            set metadata to metadata & "max_change_before=" & maxChangeBefore & linefeed
            set metadata to metadata & "max_change_after=" & maxChangeAfter & linefeed
            set metadata to metadata & "full_rebuild_invoked=true" & linefeed
            set metadata to metadata & "saved=true" & linefeed
            set metadata to metadata & "owned_workbook_closed=true"
            return metadata
        end tell
    end timeout
end run
