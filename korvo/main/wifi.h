#pragma once
/* Wi-Fi station (with NTP) using the configured network; opens the setup
 * access point when no network is configured or none can be joined. */
void wifi_start(void);
int wifi_rssi(void);
int wifi_connected(void);
