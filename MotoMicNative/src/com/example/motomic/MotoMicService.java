package com.example.motomic;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.net.wifi.WifiManager;
import android.os.Build;
import android.os.IBinder;
import android.os.PowerManager;

@SuppressWarnings("deprecation")
public class MotoMicService extends Service {

    private static final String CHANNEL_ID = "motomic_bg_channel";
    private static final int NOTIF_ID = 1001;

    private PowerManager.WakeLock wakeLock = null;
    private WifiManager.WifiLock wifiLock = null;

    @Override
    public void onCreate() {
        super.onCreate();
        acquireLocks();
        startInForeground();
        MainActivity.ensureBackgroundServersFromService(getApplicationContext());
    }

    public static final String ACTION_EXIT_APP = "com.example.motomic.ACTION_EXIT_APP";

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null && ACTION_EXIT_APP.equals(intent.getAction())) {
            releaseLocks();
            try {
                stopForeground(true);
            } catch (Exception ignored) {}
            stopSelf();
            MainActivity.exitAppFromNotification();
            return START_NOT_STICKY;
        }
        acquireLocks();
        startInForeground();
        MainActivity.ensureBackgroundServersFromService(getApplicationContext());
        return START_STICKY;
    }

    private void startInForeground() {
        try {
            NotificationManager nm = (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O && nm != null) {
                NotificationChannel chan = new NotificationChannel(
                        CHANNEL_ID,
                        "Webcam Master Background Service",
                        NotificationManager.IMPORTANCE_LOW
                );
                chan.setDescription("Keeps Microphone, Camera & Offline Wireless Discovery active across all apps");
                nm.createNotificationChannel(chan);
            }

            Intent launchIntent = new Intent(this, MainActivity.class);
            launchIntent.setFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP | Intent.FLAG_ACTIVITY_CLEAR_TOP);
            int piFlags = Build.VERSION.SDK_INT >= Build.VERSION_CODES.M
                    ? PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE
                    : PendingIntent.FLAG_UPDATE_CURRENT;
            PendingIntent contentIntent = PendingIntent.getActivity(this, 0, launchIntent, piFlags);

            Intent exitIntent = new Intent(this, MotoMicService.class);
            exitIntent.setAction(ACTION_EXIT_APP);
            PendingIntent exitPendingIntent = PendingIntent.getService(this, 1, exitIntent, piFlags);

            Notification.Builder builder;
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                builder = new Notification.Builder(this, CHANNEL_ID);
            } else {
                builder = new Notification.Builder(this);
            }

            Notification notif = builder
                    .setContentTitle("📹🎙️ Webcam Master Active (Offline Wireless ON)")
                    .setContentText("Streaming live to PC — Tap 'Exit App' to close completely")
                    .setSmallIcon(R.mipmap.ic_launcher)
                    .setContentIntent(contentIntent)
                    .addAction(android.R.drawable.ic_menu_close_clear_cancel, "🚪 Exit App", exitPendingIntent)
                    .setOngoing(true)
                    .build();

            if (Build.VERSION.SDK_INT >= 30) {
                try {
                    startForeground(
                            NOTIF_ID,
                            notif,
                            ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE | ServiceInfo.FOREGROUND_SERVICE_TYPE_CAMERA
                    );
                    return;
                } catch (Exception ignored) {}
                try {
                    startForeground(
                            NOTIF_ID,
                            notif,
                            ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE
                    );
                    return;
                } catch (Exception ignored) {}
            }
            startForeground(NOTIF_ID, notif);
        } catch (Exception ignored) {}
    }

    private void acquireLocks() {
        try {
            if (wakeLock == null) {
                PowerManager pm = (PowerManager) getSystemService(Context.POWER_SERVICE);
                if (pm != null) {
                    wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "MotoMic::BgStreamWakeLock");
                    wakeLock.setReferenceCounted(false);
                }
            }
            if (wakeLock != null && !wakeLock.isHeld()) {
                wakeLock.acquire();
            }
        } catch (Exception ignored) {}

        try {
            if (wifiLock == null) {
                WifiManager wm = (WifiManager) getApplicationContext().getSystemService(Context.WIFI_SERVICE);
                if (wm != null) {
                    wifiLock = wm.createWifiLock(WifiManager.WIFI_MODE_FULL_HIGH_PERF, "MotoMic::BgWifiLock");
                    wifiLock.setReferenceCounted(false);
                }
            }
            if (wifiLock != null && !wifiLock.isHeld()) {
                wifiLock.acquire();
            }
        } catch (Exception ignored) {}
    }

    private void releaseLocks() {
        try {
            if (wakeLock != null && wakeLock.isHeld()) {
                wakeLock.release();
            }
        } catch (Exception ignored) {}
        try {
            if (wifiLock != null && wifiLock.isHeld()) {
                wifiLock.release();
            }
        } catch (Exception ignored) {}
    }

    @Override
    public void onDestroy() {
        releaseLocks();
        try {
            stopForeground(true);
        } catch (Exception ignored) {}
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}
