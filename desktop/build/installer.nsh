; Extra steps for the Windows installer (electron-builder includes this file).
!macro customUnInstall
  ; Runs after the normal file removal. Bea's browser writes a few data files into its own
  ; folder at runtime that the uninstaller's file list doesn't know about, which leaves the
  ; folders behind. Remove the built-in server folder, then the (now empty) parents.
  RMDir /r "$INSTDIR\resources\bea-server"
  RMDir "$INSTDIR\resources"
  SetOutPath "$TEMP" ; the uninstaller's working folder would otherwise keep $INSTDIR locked
  RMDir "$INSTDIR"
!macroend
