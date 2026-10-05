#pragma once
/* Wi-Fi setup without a computer: an open access point "Korvo-Setup-XXXX"
 * with a captive page (plain HTML form) for the network, its password, the
 * server URL, the name and the room. Saving restarts the device. */
/* by_user: opened on purpose (SET key, no Wi-Fi configured): stays open
 * 5 minutes even if the network comes back. */
void portal_start(int by_user);
void portal_stop(void);
int portal_active(void);
int portal_auto_closable(void);
int portal_clients(void);
