"""UI strings in French and English (picked from the system locale)."""

from PySide6.QtCore import QLocale

STRINGS = {
    "en": {
        "listening": "Listening…", "thinking": "Thinking…", "speaking": "Speaking…", "idle": "Lapin",
        "connecting": "Connecting…", "offline": "Not connected to the server",
        "pending": "Waiting for approval on the server's Devices page",
        "online": "Connected", "error": "Error",
        "type_here": "Type a request…", "yes": "Yes", "no": "No",
        "no_speech": "I didn't hear anything.", "cancelled": "Cancelled.", "done": "Done",
        "mic_muted": "The microphone is muted.", "mic_error": "Microphone unavailable: {}",
        "hint": "Esc to close", "talk": "Talk", "settings": "Settings…", "quit": "Quit",
        "stop_audio": "Stop audio",
        "settings_title": "Lapin settings", "server_url": "Server URL", "device_name": "Device name",
        "owner": "Owner", "owner_hint": "household user this computer belongs to (e.g. alice)",
        "hotkey": "Shortcut", "menu_key": "Menu key", "menu_key_tip": "The key right of the space bar (between AltGr and Ctrl)", "autostart": "Start at login", "speak_typed": "Speak answers to typed requests",
        "language": "Language", "lang_auto": "Automatic", "status": "Status", "device_id": "Device id",
        "wayland_hotkey": "Global shortcuts can't be grabbed by apps on Wayland: bind the command\n{}\n"
                          "to a shortcut in your desktop's keyboard settings.",
        "hotkey_unavailable": "Global shortcut unavailable: {}",
        "save": "Save", "cancel": "Cancel", "tray_tip": "Lapin voice assistant",
    },
    "fr": {
        "listening": "J'écoute…", "thinking": "Je réfléchis…", "speaking": "Je parle…", "idle": "Lapin",
        "connecting": "Connexion…", "offline": "Pas connecté au serveur",
        "pending": "En attente d'approbation sur la page Appareils du serveur",
        "online": "Connecté", "error": "Erreur",
        "type_here": "Écrivez une demande…", "yes": "Oui", "no": "Non",
        "no_speech": "Je n'ai rien entendu.", "cancelled": "Annulé.", "done": "C'est fait",
        "mic_muted": "Le micro est coupé.", "mic_error": "Micro indisponible : {}",
        "hint": "Échap pour fermer", "talk": "Parler", "settings": "Réglages…", "quit": "Quitter",
        "stop_audio": "Arrêter le son",
        "settings_title": "Réglages de Lapin", "server_url": "Adresse du serveur", "device_name": "Nom de l'appareil",
        "owner": "Propriétaire", "owner_hint": "personne du foyer à qui appartient cet ordinateur (ex. alice)",
        "hotkey": "Raccourci", "menu_key": "Touche Menu", "menu_key_tip": "La touche à droite de la barre d'espace (entre AltGr et Ctrl)", "autostart": "Lancer à l'ouverture de session",
        "speak_typed": "Lire à voix haute les réponses aux demandes écrites",
        "language": "Langue", "lang_auto": "Automatique", "status": "État", "device_id": "Identifiant",
        "wayland_hotkey": "Sous Wayland, une application ne peut pas capturer de raccourci global : associez la "
                          "commande\n{}\nà un raccourci dans les réglages clavier du bureau.",
        "hotkey_unavailable": "Raccourci global indisponible : {}",
        "save": "Enregistrer", "cancel": "Annuler", "tray_tip": "Assistant vocal Lapin",
    },
}

# confirmation summaries of our own tools (the server fills "{action} the computer")
SUMMARIES = {
    "en": {"shutdown the computer": "Shut down the computer", "reboot the computer": "Restart the computer",
           "suspend the computer": "Put the computer to sleep", "logout the computer": "Log out of the session"},
    "fr": {"shutdown the computer": "Éteindre l'ordinateur", "reboot the computer": "Redémarrer l'ordinateur",
           "suspend the computer": "Mettre l'ordinateur en veille", "logout the computer": "Fermer la session"},
}

_lang = "en"


def setup(choice="auto"):
    global _lang
    if choice in STRINGS:
        _lang = choice
    else:
        _lang = "fr" if QLocale.system().name().lower().startswith("fr") else "en"
    return _lang


def lang():
    return _lang


def tr(key, *args):
    s = STRINGS[_lang].get(key) or STRINGS["en"].get(key) or key
    return s.format(*args) if args else s


def confirm_text(summary):
    """The question shown next to the Yes/No buttons."""
    summary = (summary or "").strip()
    if not summary:
        return ""
    s = SUMMARIES[_lang].get(summary.lower()) or summary[:1].upper() + summary[1:]
    if s.endswith("?"):
        return s
    return s + (" ?" if _lang == "fr" else "?")
