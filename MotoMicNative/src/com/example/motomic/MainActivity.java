package com.example.motomic;

import android.Manifest;
import android.app.Activity;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.ImageFormat;
import android.graphics.Matrix;
import android.graphics.Paint;
import android.graphics.Rect;
import android.graphics.RectF;
import android.graphics.SurfaceTexture;
import android.graphics.Typeface;
import android.graphics.YuvImage;
import android.graphics.drawable.GradientDrawable;
import android.hardware.Camera;
import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.MediaRecorder;
import android.media.audiofx.AcousticEchoCanceler;
import android.media.audiofx.NoiseSuppressor;
import android.net.Uri;
import android.net.wifi.WifiInfo;
import android.net.wifi.WifiManager;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.PowerManager;
import android.provider.Settings;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.SeekBar;
import android.widget.TextView;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.Inet4Address;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.InterfaceAddress;
import java.net.NetworkInterface;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.util.ArrayList;
import java.util.Enumeration;
import java.util.List;
import java.util.Locale;

@SuppressWarnings("deprecation")
public class MainActivity extends Activity {

    private static final int REQ_PERMISSIONS = 101;
    private static final int WIRELESS_SERVER_PORT = 8988;
    private static final int PC_SERVER_PORT = 8989;
    private static final int DISCOVERY_PORT = 8990;

    private static final byte PACKET_AUDIO = 1;
    private static final byte PACKET_HANDSHAKE = 2;
    private static final byte PACKET_PING = 3;
    private static final byte PACKET_VIDEO = 4;
    private static final byte PACKET_CONTROL = 5;

    private static final Handler mainHandler = new Handler(Looper.getMainLooper());
    private static volatile MainActivity activeActivity = null;
    private static volatile Context appContext = null;

    // Persistent Static Engine State (Survives app switching, backgrounding, and screen lock)
    private static volatile String appMode = "WEBCAM_MIC";
    private static volatile String cameraSource = "PHONE";
    private static volatile boolean isStreaming = false;
    private static volatile boolean userManuallyStopped = false;
    private static volatile boolean isConnected = false;
    private static volatile String connectionStatus = "Ready (Works Offline — No Internet Required)";
    private static volatile String phoneWifiIp = "192.168.1.65";
    private static volatile String wirelessNetMode = "Offline Wireless";
    private static volatile String connectedPcName = "None";
    private static volatile String connectedPcIp = "";
    private static volatile int streamToken = 0;

    // Audio State
    private static volatile int sampleRate = 48000;
    private static volatile float gain = 1.0f;
    private static volatile boolean isMuted = false;
    private static volatile float currentDb = -60.0f;
    private static volatile float peakDb = -60.0f;
    private static volatile long packetsSent = 0L;
    private static final float[] waveform = new float[64];

    // Clean Camera State (Zero Filters)
    private static volatile boolean cameraEnabled = true;
    private static volatile boolean useFrontCamera = true;
    private static volatile long videoFramesSent = 0L;
    private static volatile byte[] latestVideoJpeg = null;
    private static volatile long latestVideoSeq = 0L;
    private static volatile long lastCameraFrameMs = 0L;
    private static volatile Bitmap latestPreviewBitmap = null;

    private static Camera camera = null;
    private static SurfaceTexture dummySurface = null;
    private static volatile boolean isProcessingFrame = false;

    private static Thread streamerThread = null;
    private static ServerSocket wirelessServerSocket = null;
    private static DatagramSocket discoveryUdpSocket = null;
    private static volatile Socket acceptedWirelessSocket = null;
    private static volatile Socket currentActiveSocket = null;
    private static volatile boolean blinkDotOn = true;
    private static volatile boolean blinkLoopStarted = false;

    // UI References (Bound to current visible MainActivity instance)
    private TextView statusBadge;
    private TextView wifiIpInfo;
    private TextView connectedDeviceBadge;
    private TextView connStatusText;
    private final Button[] appModeButtons = new Button[3];
    private final Button[] camSourceButtons = new Button[3];
    private LinearLayout cameraSectionCard;
    private CameraPreviewView cameraPreviewView;
    private TextView camTitleText;
    private Button camFlipBtn;
    private Button startStopToggleBtn;
    private Button muteToggleBtn;
    private TextView metricsText;
    private WaveformView waveformView;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        activeActivity = this;
        appContext = getApplicationContext();

        getWindow().addFlags(
                WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON |
                WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED |
                WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON
        );
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O_MR1) {
            setShowWhenLocked(true);
            setTurnScreenOn(true);
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
            getWindow().setStatusBarColor(Color.parseColor("#090D16"));
            getWindow().setNavigationBarColor(Color.parseColor("#090D16"));
        }

        phoneWifiIp = detectWifiIp(appContext);
        buildUserInterface();
        startBlinkingIndicatorLoop();
        ensureWirelessServerRunning();
        ensureOfflineDiscoveryRunning();
        handleControlIntent(getIntent());

        if (!hasRequiredPermissions()) {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                requestPermissions(new String[]{
                        Manifest.permission.RECORD_AUDIO,
                        Manifest.permission.CAMERA
                }, REQ_PERMISSIONS);
            }
        } else {
            if (!isStreaming && !userManuallyStopped) {
                startStreaming();
            } else {
                updateControlButtons();
                refreshUi();
            }
        }
    }

    private static synchronized void startBlinkingIndicatorLoop() {
        if (blinkLoopStarted) return;
        blinkLoopStarted = true;
        final Runnable blinkRunnable = new Runnable() {
            @Override
            public void run() {
                blinkDotOn = !blinkDotOn;
                if (isStreaming) {
                    refreshUiStatic();
                }
                mainHandler.postDelayed(this, 450);
            }
        };
        mainHandler.postDelayed(blinkRunnable, 450);
    }

    public static void exitAppFromNotification() {
        MainActivity act = activeActivity;
        if (act != null) {
            act.exitAppCompletely();
        } else {
            userManuallyStopped = true;
            stopStreaming();
            System.exit(0);
        }
    }

    private void exitAppCompletely() {
        userManuallyStopped = true;
        stopStreaming();
        try {
            if (wirelessServerSocket != null) {
                wirelessServerSocket.close();
                wirelessServerSocket = null;
            }
        } catch (Exception ignored) {}
        try {
            if (discoveryUdpSocket != null) {
                discoveryUdpSocket.close();
                discoveryUdpSocket = null;
            }
        } catch (Exception ignored) {}
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
                finishAndRemoveTask();
            } else {
                finish();
            }
        } catch (Exception ignored) {}
        System.exit(0);
    }

    @Override
    protected void onResume() {
        super.onResume();
        activeActivity = this;
        phoneWifiIp = detectWifiIp(appContext);
        if (isStreaming && cameraEnabled && camera == null) {
            startCamera();
        }
        updateControlButtons();
        refreshUi();
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        handleControlIntent(intent);
    }

    private static String getFriendlyDeviceName() {
        String man = Build.MANUFACTURER != null ? Build.MANUFACTURER.trim() : "";
        String mod = Build.MODEL != null ? Build.MODEL.trim() : "Android Phone";
        String combined;
        if (mod.toLowerCase(Locale.US).startsWith(man.toLowerCase(Locale.US))) {
            combined = mod;
        } else {
            combined = (man + " " + mod).trim();
        }
        // Capitalize words nicely (e.g., "Motorola Edge 50 Neo")
        String[] parts = combined.split("\\s+");
        StringBuilder sb = new StringBuilder();
        for (String p : parts) {
            if (p.length() > 0) {
                if (sb.length() > 0) sb.append(" ");
                sb.append(Character.toUpperCase(p.charAt(0)));
                if (p.length() > 1) sb.append(p.substring(1));
            }
        }
        return sb.length() > 0 ? sb.toString() : "Motorola Edge 50 Neo";
    }

    private void handleControlIntent(Intent intent) {
        if (intent == null) return;
        if (intent.hasExtra("app_mode")) {
            String am = intent.getStringExtra("app_mode");
            if (am != null) setAppUsageMode(am);
        }
        updateControlButtons();
        if (intent.hasExtra("auto_start")) {
            boolean autoStart = intent.getBooleanExtra("auto_start", true);
            if (autoStart && hasRequiredPermissions()) {
                userManuallyStopped = false;
                if (!isStreaming) startStreaming();
            }
        }
    }

    private static void sendControlToPc(final String jsonStr) {
        new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    Socket s = currentActiveSocket;
                    if (s != null && !s.isClosed()) {
                        byte[] payload = jsonStr.getBytes("UTF-8");
                        byte[] hdr = buildHeader(PACKET_CONTROL, (byte) 1, sampleRate, 0, payload.length);
                        OutputStream out = s.getOutputStream();
                        synchronized (s) {
                            out.write(hdr);
                            out.write(payload);
                            out.flush();
                        }
                    }
                } catch (Exception ignored) {}
            }
        }).start();
    }

    private static void setCameraSourceChoice(String src, boolean notifyPc) {
        cameraSource = src;
        if ("OFF".equals(src)) {
            appMode = "MIC_ONLY";
            cameraEnabled = false;
            isMuted = false;
            stopCamera();
        } else if ("PHONE".equals(src)) {
            if ("MIC_ONLY".equals(appMode)) {
                appMode = "WEBCAM_MIC";
            }
            cameraEnabled = true;
            if (isStreaming) startCamera();
        } else {
            if ("MIC_ONLY".equals(appMode)) {
                appMode = "WEBCAM_MIC";
            }
            cameraEnabled = false;
            stopCamera();
        }
        if (notifyPc) {
            sendControlToPc("{\"cmd\":\"camera_source\",\"value\":\"" + src + "\"}");
        }
        MainActivity act = activeActivity;
        if (act != null) act.updateControlButtons();
    }

    private static void setAppUsageMode(String mode) {
        setAppUsageMode(mode, true);
    }

    private static void setAppUsageMode(String mode, boolean notifyPc) {
        appMode = mode;
        if ("MIC_ONLY".equals(mode)) {
            cameraSource = "OFF";
            cameraEnabled = false;
            isMuted = false;
            stopCamera();
        } else if ("WEBCAM_ONLY".equals(mode)) {
            if ("OFF".equals(cameraSource)) cameraSource = "PHONE";
            cameraEnabled = "PHONE".equals(cameraSource);
            isMuted = true;
            if (cameraEnabled && isStreaming) startCamera();
            else stopCamera();
        } else {
            appMode = "WEBCAM_MIC";
            if ("OFF".equals(cameraSource)) cameraSource = "PHONE";
            cameraEnabled = "PHONE".equals(cameraSource);
            isMuted = false;
            if (cameraEnabled && isStreaming) startCamera();
            else stopCamera();
        }
        if (notifyPc) {
            sendControlToPc("{\"cmd\":\"app_mode\",\"value\":\"" + appMode + "\"}");
        }
        MainActivity act = activeActivity;
        if (act != null) act.updateControlButtons();
    }

    private boolean hasRequiredPermissions() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            boolean mic = checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED;
            boolean cam = checkSelfPermission(Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED;
            return mic && cam;
        }
        return true;
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode == REQ_PERMISSIONS && hasRequiredPermissions()) {
            userManuallyStopped = false;
            startStreaming();
        }
    }

    /**
     * Detects the phone's active local wireless IP across:
     * 1. Portable Hotspot AP Mode (ap0, wlan1, softap0, swlan0) — works 100% WITHOUT Internet or Router!
     * 2. Local Wi-Fi / PC Mobile Hotspot (wlan0) — works 100% WITHOUT Internet!
     * 3. Bluetooth PAN / USB Tethering (bt-pan, rndis0, ncm0)
     * Ignores cellular mobile data interfaces (rmnet*, ccmni*).
     */
    private static String detectWifiIp(Context ctx) {
        String hotspotIp = null;
        String wlanIp = null;
        String otherLocalIp = null;

        try {
            Enumeration<NetworkInterface> nets = NetworkInterface.getNetworkInterfaces();
            while (nets != null && nets.hasMoreElements()) {
                NetworkInterface ni = nets.nextElement();
                if (!ni.isUp() || ni.isLoopback()) continue;
                String name = ni.getName() != null ? ni.getName().toLowerCase(Locale.US) : "";
                // Skip cellular data interfaces so offline local wireless is always chosen
                if (name.startsWith("rmnet") || name.startsWith("ccmni") || name.startsWith("dummy")) {
                    continue;
                }
                Enumeration<InetAddress> addrs = ni.getInetAddresses();
                while (addrs.hasMoreElements()) {
                    InetAddress addr = addrs.nextElement();
                    if (addr instanceof Inet4Address && !addr.isLoopbackAddress()) {
                        String ip = addr.getHostAddress();
                        if (ip == null || ip.startsWith("169.254.")) continue;
                        if (name.contains("ap") || name.contains("wlan1") || name.contains("swlan") || name.contains("rndis") || ip.startsWith("192.168.43.") || ip.startsWith("192.168.215.")) {
                            hotspotIp = ip;
                        } else if (name.contains("wlan") || name.contains("p2p")) {
                            wlanIp = ip;
                        } else if (ip.startsWith("192.168.") || ip.startsWith("10.") || ip.startsWith("172.")) {
                            otherLocalIp = ip;
                        }
                    }
                }
            }
        } catch (Exception ignored) {}

        if (hotspotIp != null) {
            wirelessNetMode = "📡 Phone Hotspot (Offline — No Internet Needed)";
            return hotspotIp;
        }
        if (wlanIp != null) {
            if (wlanIp.startsWith("192.168.137.")) {
                wirelessNetMode = "📡 PC Hotspot Direct (Offline — No Internet Needed)";
            } else {
                wirelessNetMode = "📶 Local Wireless (Works Without Internet)";
            }
            return wlanIp;
        }

        try {
            if (ctx != null) {
                WifiManager wm = (WifiManager) ctx.getApplicationContext().getSystemService(Context.WIFI_SERVICE);
                if (wm != null) {
                    WifiInfo info = wm.getConnectionInfo();
                    int ipInt = info.getIpAddress();
                    if (ipInt != 0) {
                        wirelessNetMode = "📶 Local Wireless (Works Without Internet)";
                        return String.format(Locale.US, "%d.%d.%d.%d",
                                (ipInt & 0xff), (ipInt >> 8 & 0xff),
                                (ipInt >> 16 & 0xff), (ipInt >> 24 & 0xff));
                    }
                }
            }
        } catch (Exception ignored) {}

        if (otherLocalIp != null) {
            wirelessNetMode = "📡 Offline Direct Link";
            return otherLocalIp;
        }
        return "127.0.0.1";
    }

    private static List<InetAddress> getLocalBroadcastAddresses() {
        List<InetAddress> list = new ArrayList<>();
        try {
            list.add(InetAddress.getByName("255.255.255.255"));
            list.add(InetAddress.getByName("192.168.137.1")); // Windows PC Mobile Hotspot default IP
        } catch (Exception ignored) {}
        try {
            Enumeration<NetworkInterface> nets = NetworkInterface.getNetworkInterfaces();
            while (nets != null && nets.hasMoreElements()) {
                NetworkInterface ni = nets.nextElement();
                if (!ni.isUp() || ni.isLoopback()) continue;
                String name = ni.getName() != null ? ni.getName().toLowerCase(Locale.US) : "";
                if (name.startsWith("rmnet") || name.startsWith("ccmni")) continue;
                for (InterfaceAddress ia : ni.getInterfaceAddresses()) {
                    InetAddress bcast = ia.getBroadcast();
                    if (bcast instanceof Inet4Address && !list.contains(bcast)) {
                        list.add(bcast);
                    }
                }
            }
        } catch (Exception ignored) {}
        return list;
    }

    private int dp(int v) {
        return (int) TypedValue.applyDimension(
                TypedValue.COMPLEX_UNIT_DIP, v, getResources().getDisplayMetrics()
        );
    }

    private GradientDrawable roundedBg(String hexColor, int radiusDp) {
        GradientDrawable gd = new GradientDrawable();
        gd.setColor(Color.parseColor(hexColor));
        gd.setCornerRadius(dp(radiusDp));
        return gd;
    }

    private void buildUserInterface() {
        ScrollView scroll = new ScrollView(this);
        scroll.setFillViewport(true);
        scroll.setBackgroundColor(Color.parseColor("#090D16"));

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        int pad = dp(14);
        root.setPadding(pad, pad, pad, pad);
        scroll.addView(root);

        LinearLayout.LayoutParams cardLp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT
        );
        cardLp.bottomMargin = dp(10);

        // 1. Header & Connected PC Info Card
        LinearLayout topCard = new LinearLayout(this);
        topCard.setOrientation(LinearLayout.VERTICAL);
        topCard.setBackground(roundedBg("#1E293B", 14));
        topCard.setPadding(dp(14), dp(12), dp(14), dp(12));
        root.addView(topCard, cardLp);

        LinearLayout headerRow = new LinearLayout(this);
        headerRow.setOrientation(LinearLayout.HORIZONTAL);
        headerRow.setGravity(Gravity.CENTER_VERTICAL);
        topCard.addView(headerRow, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT
        ));

        android.widget.ImageView iconView = new android.widget.ImageView(this);
        iconView.setImageResource(R.mipmap.ic_launcher);
        LinearLayout.LayoutParams iconLp = new LinearLayout.LayoutParams(dp(42), dp(42));
        iconLp.rightMargin = dp(10);
        headerRow.addView(iconView, iconLp);

        LinearLayout titleCol = new LinearLayout(this);
        titleCol.setOrientation(LinearLayout.VERTICAL);
        headerRow.addView(titleCol, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));

        TextView title = new TextView(this);
        title.setText("Webcam Master");
        title.setTextColor(Color.WHITE);
        title.setTextSize(18);
        title.setTypeface(Typeface.DEFAULT_BOLD);
        titleCol.addView(title);

        wifiIpInfo = new TextView(this);
        wifiIpInfo.setText("📱 " + getFriendlyDeviceName() + " • " + phoneWifiIp);
        wifiIpInfo.setTextColor(Color.parseColor("#34D399"));
        wifiIpInfo.setTextSize(11);
        wifiIpInfo.setTypeface(Typeface.DEFAULT_BOLD);
        titleCol.addView(wifiIpInfo);

        statusBadge = new TextView(this);
        statusBadge.setText("● LIVE (ON)");
        statusBadge.setTextColor(Color.parseColor("#10B981"));
        statusBadge.setTextSize(11);
        statusBadge.setTypeface(Typeface.DEFAULT_BOLD);
        statusBadge.setPadding(dp(10), dp(6), dp(10), dp(6));
        statusBadge.setBackground(roundedBg("#064E3B", 14));
        headerRow.addView(statusBadge);

        Button topExitBtn = new Button(this);
        topExitBtn.setText("🚪 Exit");
        topExitBtn.setTextSize(11);
        topExitBtn.setTypeface(Typeface.DEFAULT_BOLD);
        topExitBtn.setAllCaps(false);
        topExitBtn.setTextColor(Color.WHITE);
        topExitBtn.setBackground(roundedBg("#EF4444", 10));
        topExitBtn.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                exitAppCompletely();
            }
        });
        LinearLayout.LayoutParams topExitLp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, dp(34)
        );
        topExitLp.leftMargin = dp(6);
        headerRow.addView(topExitBtn, topExitLp);

        // Single Clean Connected PC Status Banner
        connectedDeviceBadge = new TextView(this);
        connectedDeviceBadge.setText("🟡 💻 Waiting for PC Connection (" + phoneWifiIp + ":" + WIRELESS_SERVER_PORT + ")\n📡 Works Offline (No Internet Needed) & Continues in Background");
        connectedDeviceBadge.setTextColor(Color.parseColor("#F8FAFC"));
        connectedDeviceBadge.setTextSize(12);
        connectedDeviceBadge.setTypeface(Typeface.DEFAULT_BOLD);
        connectedDeviceBadge.setPadding(dp(12), dp(8), dp(12), dp(8));
        connectedDeviceBadge.setBackground(roundedBg("#0F172A", 10));
        LinearLayout.LayoutParams cdLp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT
        );
        cdLp.topMargin = dp(10);
        cdLp.bottomMargin = dp(8);
        topCard.addView(connectedDeviceBadge, cdLp);

        connStatusText = new TextView(this);
        connStatusText.setVisibility(View.GONE);
        topCard.addView(connStatusText);

        // Step 1: 3-Mode Selector Bar (Mic Only | Cam + Mic | Cam Only)
        TextView modeLabel = new TextView(this);
        modeLabel.setText("1. Select Streaming Mode:");
        modeLabel.setTextColor(Color.parseColor("#CBD5E1"));
        modeLabel.setTextSize(12);
        modeLabel.setTypeface(Typeface.DEFAULT_BOLD);
        modeLabel.setPadding(dp(2), 0, 0, dp(4));
        topCard.addView(modeLabel);

        LinearLayout modeRow = new LinearLayout(this);
        modeRow.setOrientation(LinearLayout.HORIZONTAL);
        modeRow.setBackground(roundedBg("#0F172A", 10));
        modeRow.setPadding(dp(4), dp(4), dp(4), dp(4));
        topCard.addView(modeRow, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT
        ));

        final String[] modeLabels = new String[]{"🎙️ Mic Only", "📹 Cam + Mic", "📷 Cam Only"};
        final String[] modeCodes = new String[]{"MIC_ONLY", "WEBCAM_MIC", "WEBCAM_ONLY"};
        for (int i = 0; i < 3; i++) {
            final String code = modeCodes[i];
            Button b = new Button(this);
            b.setText(modeLabels[i]);
            b.setTextSize(12);
            b.setTypeface(Typeface.DEFAULT_BOLD);
            b.setAllCaps(false);
            b.setTextColor(Color.WHITE);
            LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(0, dp(40), 1f);
            if (i < 2) lp.rightMargin = dp(4);
            b.setOnClickListener(new View.OnClickListener() {
                @Override
                public void onClick(View v) {
                    setAppUsageMode(code);
                }
            });
            appModeButtons[i] = b;
            modeRow.addView(b, lp);
        }

        // Camera Source Choice (Mobile Camera vs PC Camera)
        TextView camSrcLabel = new TextView(this);
        camSrcLabel.setText("📹 Camera Choice (Mobile Phone vs PC Webcam):");
        camSrcLabel.setTextColor(Color.parseColor("#CBD5E1"));
        camSrcLabel.setTextSize(12);
        camSrcLabel.setTypeface(Typeface.DEFAULT_BOLD);
        LinearLayout.LayoutParams camSrcLblLp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT
        );
        camSrcLblLp.topMargin = dp(10);
        camSrcLblLp.bottomMargin = dp(6);
        topCard.addView(camSrcLabel, camSrcLblLp);

        LinearLayout camSrcRow = new LinearLayout(this);
        camSrcRow.setOrientation(LinearLayout.HORIZONTAL);
        topCard.addView(camSrcRow, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT
        ));

        final String[] srcLabels = new String[]{"📱 Mobile Cam", "💻 PC Cam", "🚫 Cam Off"};
        final String[] srcCodes = new String[]{"PHONE", "PC_0", "OFF"};
        for (int i = 0; i < 3; i++) {
            final String sCode = srcCodes[i];
            Button sb = new Button(this);
            sb.setText(srcLabels[i]);
            sb.setTextSize(11);
            sb.setTypeface(Typeface.DEFAULT_BOLD);
            sb.setAllCaps(false);
            sb.setTextColor(Color.WHITE);
            sb.setBackground(roundedBg("#334155", 8));
            LinearLayout.LayoutParams slp = new LinearLayout.LayoutParams(0, dp(38), 1f);
            if (i < 2) slp.rightMargin = dp(4);
            sb.setOnClickListener(new View.OnClickListener() {
                @Override
                public void onClick(View v) {
                    setCameraSourceChoice(sCode, true);
                }
            });
            camSourceButtons[i] = sb;
            camSrcRow.addView(sb, slp);
        }

        // 2. Clean Camera Preview Card (Automatically hidden when in Mic Only mode)
        cameraSectionCard = new LinearLayout(this);
        cameraSectionCard.setOrientation(LinearLayout.VERTICAL);
        cameraSectionCard.setBackground(roundedBg("#1E293B", 14));
        cameraSectionCard.setPadding(dp(14), dp(10), dp(14), dp(12));
        root.addView(cameraSectionCard, cardLp);

        LinearLayout camHeader = new LinearLayout(this);
        camHeader.setOrientation(LinearLayout.HORIZONTAL);
        camHeader.setGravity(Gravity.CENTER_VERTICAL);
        cameraSectionCard.addView(camHeader, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT
        ));

        camTitleText = new TextView(this);
        camTitleText.setText("📹 2. Clean HD Camera (Front)");
        camTitleText.setTextColor(Color.WHITE);
        camTitleText.setTextSize(13);
        camTitleText.setTypeface(Typeface.DEFAULT_BOLD);
        camHeader.addView(camTitleText, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));

        camFlipBtn = new Button(this);
        camFlipBtn.setText("🔄 Flip Front / Back");
        camFlipBtn.setTextSize(11);
        camFlipBtn.setTypeface(Typeface.DEFAULT_BOLD);
        camFlipBtn.setAllCaps(false);
        camFlipBtn.setTextColor(Color.WHITE);
        camFlipBtn.setBackground(roundedBg("#0284C7", 8));
        camFlipBtn.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                useFrontCamera = !useFrontCamera;
                restartCamera();
                updateControlButtons();
            }
        });
        camHeader.addView(camFlipBtn, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, dp(34)));

        cameraPreviewView = new CameraPreviewView(this);
        LinearLayout.LayoutParams prevLp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, dp(250)
        );
        prevLp.topMargin = dp(8);
        cameraSectionCard.addView(cameraPreviewView, prevLp);

        // 3. Microphone & Master Stream Control Card
        LinearLayout audioCard = new LinearLayout(this);
        audioCard.setOrientation(LinearLayout.VERTICAL);
        audioCard.setBackground(roundedBg("#1E293B", 14));
        audioCard.setPadding(dp(14), dp(12), dp(14), dp(14));
        root.addView(audioCard, cardLp);

        metricsText = new TextView(this);
        metricsText.setText("🎙️ 3. Microphone Level: -60.0 dB  |  Frames: 0");
        metricsText.setTextColor(Color.parseColor("#22D3EE"));
        metricsText.setTextSize(12);
        metricsText.setTypeface(Typeface.MONOSPACE, Typeface.BOLD);
        audioCard.addView(metricsText);

        waveformView = new WaveformView(this);
        LinearLayout.LayoutParams waveLp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, dp(56)
        );
        waveLp.topMargin = dp(6);
        waveLp.bottomMargin = dp(10);
        audioCard.addView(waveformView, waveLp);

        // Gain Slider & Mute Row
        LinearLayout gainRow = new LinearLayout(this);
        gainRow.setOrientation(LinearLayout.HORIZONTAL);
        gainRow.setGravity(Gravity.CENTER_VERTICAL);
        LinearLayout.LayoutParams gainRowLp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT
        );
        gainRowLp.bottomMargin = dp(12);
        audioCard.addView(gainRow, gainRowLp);

        final TextView gainLbl = new TextView(this);
        gainLbl.setText("Mic Vol: " + Math.round(gain * 100) + "%");
        gainLbl.setTextColor(Color.parseColor("#CBD5E1"));
        gainLbl.setTextSize(11);
        gainLbl.setTypeface(Typeface.DEFAULT_BOLD);
        gainRow.addView(gainLbl);

        SeekBar gainSeek = new SeekBar(this);
        gainSeek.setMax(300);
        gainSeek.setProgress(Math.round(gain * 100));
        gainSeek.setOnSeekBarChangeListener(new SeekBar.OnSeekBarChangeListener() {
            @Override
            public void onProgressChanged(SeekBar seekBar, int progress, boolean fromUser) {
                int p = Math.max(20, progress);
                gain = p / 100.0f;
                gainLbl.setText("Mic Vol: " + p + "%");
            }
            @Override public void onStartTrackingTouch(SeekBar seekBar) {}
            @Override public void onStopTrackingTouch(SeekBar seekBar) {}
        });
        gainRow.addView(gainSeek, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));

        muteToggleBtn = new Button(this);
        muteToggleBtn.setText("🔊 Mic ON");
        muteToggleBtn.setTextSize(11);
        muteToggleBtn.setTypeface(Typeface.DEFAULT_BOLD);
        muteToggleBtn.setAllCaps(false);
        muteToggleBtn.setTextColor(Color.WHITE);
        muteToggleBtn.setBackground(roundedBg("#334155", 10));
        muteToggleBtn.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                isMuted = !isMuted;
                updateControlButtons();
            }
        });
        gainRow.addView(muteToggleBtn, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, dp(34)));

        // SINGLE START / STOP TOGGLE BUTTON (Green when ACTIVE/ON, Gray when STOPPED/OFF)
        startStopToggleBtn = new Button(this);
        startStopToggleBtn.setText("🟢 STREAMING ACTIVE: ON (Tap to Stop)");
        startStopToggleBtn.setTextSize(15);
        startStopToggleBtn.setTypeface(Typeface.DEFAULT_BOLD);
        startStopToggleBtn.setAllCaps(false);
        startStopToggleBtn.setTextColor(Color.WHITE);
        startStopToggleBtn.setBackground(roundedBg("#10B981", 12));
        startStopToggleBtn.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                if (isStreaming) {
                    userManuallyStopped = true;
                    stopStreaming();
                } else {
                    userManuallyStopped = false;
                    startStreaming();
                }
            }
        });
        audioCard.addView(startStopToggleBtn, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, dp(52)
        ));

        // EXIT APP BUTTON (Cleanly shuts down background service & closes app completely)
        Button exitAppBtn = new Button(this);
        exitAppBtn.setText("🚪 Exit App (Close Completely)");
        exitAppBtn.setTextSize(13);
        exitAppBtn.setTypeface(Typeface.DEFAULT_BOLD);
        exitAppBtn.setAllCaps(false);
        exitAppBtn.setTextColor(Color.WHITE);
        exitAppBtn.setBackground(roundedBg("#EF4444", 10));
        exitAppBtn.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                exitAppCompletely();
            }
        });
        LinearLayout.LayoutParams exitLp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, dp(42)
        );
        exitLp.topMargin = dp(8);
        audioCard.addView(exitAppBtn, exitLp);

        setContentView(scroll);
        updateControlButtons();
    }

    private void updateControlButtons() {
        mainHandler.post(new Runnable() {
            @Override
            public void run() {
                String[] codes = new String[]{"MIC_ONLY", "WEBCAM_MIC", "WEBCAM_ONLY"};
                for (int i = 0; i < 3; i++) {
                    if (appModeButtons[i] != null) {
                        boolean sel = codes[i].equals(appMode);
                        appModeButtons[i].setBackground(roundedBg(sel ? "#10B981" : "#334155", 8));
                    }
                }
                boolean camOff = "MIC_ONLY".equals(appMode) || "OFF".equals(cameraSource);
                boolean isPhoneCam = !camOff && "PHONE".equals(cameraSource);
                boolean isPcCam = !camOff && !"PHONE".equals(cameraSource);
                if (camSourceButtons[0] != null) {
                    camSourceButtons[0].setBackground(roundedBg(isPhoneCam ? "#10B981" : "#334155", 8));
                }
                if (camSourceButtons[1] != null) {
                    camSourceButtons[1].setBackground(roundedBg(isPcCam ? "#10B981" : "#334155", 8));
                }
                if (camSourceButtons[2] != null) {
                    camSourceButtons[2].setBackground(roundedBg(camOff ? "#EF4444" : "#334155", 8));
                }
                if (cameraSectionCard != null) {
                    cameraSectionCard.setVisibility(cameraEnabled ? View.VISIBLE : View.GONE);
                }
                if (camTitleText != null) {
                    camTitleText.setText(useFrontCamera ? "📹 2. Clean HD Camera (Front)" : "📹 2. Clean HD Camera (Back)");
                }
                if (muteToggleBtn != null) {
                    muteToggleBtn.setText(isMuted ? "🔇 Mic Muted" : "🔊 Mic ON");
                    muteToggleBtn.setBackground(roundedBg(isMuted ? "#EF4444" : "#334155", 10));
                }
                if (startStopToggleBtn != null) {
                    if (isStreaming) {
                        startStopToggleBtn.setText("🟢 STREAMING ACTIVE: ON (Tap to Stop)");
                        startStopToggleBtn.setBackground(roundedBg("#10B981", 12));
                    } else {
                        startStopToggleBtn.setText("⚪ STREAM STOPPED: OFF (Tap to Start)");
                        startStopToggleBtn.setBackground(roundedBg("#475569", 12));
                    }
                }
            }
        });
    }

    private static void refreshUiStatic() {
        MainActivity act = activeActivity;
        if (act != null) act.refreshUi();
    }

    private void refreshUi() {
        mainHandler.post(new Runnable() {
            @Override
            public void run() {
                if (statusBadge == null) return;
                if (wifiIpInfo != null) {
                    wifiIpInfo.setText("📱 " + getFriendlyDeviceName() + " • " + phoneWifiIp + ":" + WIRELESS_SERVER_PORT);
                }
                String dot = blinkDotOn ? "🔴 ● " : "⚫ ○ ";
                if (isStreaming && isConnected) {
                    statusBadge.setText(dot + "LIVE (ON)");
                    statusBadge.setTextColor(Color.parseColor(blinkDotOn ? "#34D399" : "#A7F3D0"));
                    statusBadge.setBackground(roundedBg(blinkDotOn ? "#065F46" : "#064E3B", 16));
                    if (connectedDeviceBadge != null) {
                        String pcLabel = !"None".equals(connectedPcName) && !connectedPcName.isEmpty()
                                ? (connectedPcName + " (" + connectedPcIp + ")")
                                : connectedPcIp;
                        connectedDeviceBadge.setText("🟢 💻 Connected to PC: " + pcLabel + "\n" + wirelessNetMode + "  •  Works in Background & Screen Off");
                        connectedDeviceBadge.setTextColor(Color.parseColor("#34D399"));
                        connectedDeviceBadge.setBackground(roundedBg("#064E3B", 10));
                    }
                } else if (isStreaming) {
                    statusBadge.setText(dot + "WORKING");
                    statusBadge.setTextColor(Color.parseColor(blinkDotOn ? "#34D399" : "#A7F3D0"));
                    statusBadge.setBackground(roundedBg("#064E3B", 16));
                    if (connectedDeviceBadge != null) {
                        connectedDeviceBadge.setText("🟡 💻 Waiting for PC (" + phoneWifiIp + ":" + WIRELESS_SERVER_PORT + ")\n📡 Offline Wireless Ready (Wi-Fi / Hotspot — No Internet Needed)");
                        connectedDeviceBadge.setTextColor(Color.parseColor("#FDE68A"));
                        connectedDeviceBadge.setBackground(roundedBg("#0F172A", 10));
                    }
                } else {
                    statusBadge.setText("⚪ STOPPED");
                    statusBadge.setTextColor(Color.parseColor("#CBD5E1"));
                    statusBadge.setBackground(roundedBg("#334155", 16));
                    if (connectedDeviceBadge != null) {
                        connectedDeviceBadge.setText("⚪ 💻 Stream Paused — Tap 'STREAM STOPPED' button below to start");
                        connectedDeviceBadge.setTextColor(Color.parseColor("#94A3B8"));
                        connectedDeviceBadge.setBackground(roundedBg("#0F172A", 10));
                    }
                }
                metricsText.setText(String.format(
                        Locale.US,
                        "🎙️ 3. Mic: %5.1f dB | Mode: %s | Frames: %d",
                        currentDb, appMode, videoFramesSent
                ));
                if (waveformView != null) waveformView.invalidate();
                if (cameraPreviewView != null) cameraPreviewView.invalidate();
            }
        });
    }

    // ==================== CLEAN CAMERA ENGINE (NO FILTERS) ====================

    private static synchronized void startCamera() {
        if (!cameraEnabled || camera != null) return;
        try {
            int camId = 0;
            int count = Camera.getNumberOfCameras();
            Camera.CameraInfo info = new Camera.CameraInfo();
            int targetFacing = useFrontCamera ? Camera.CameraInfo.CAMERA_FACING_FRONT : Camera.CameraInfo.CAMERA_FACING_BACK;
            for (int i = 0; i < count; i++) {
                Camera.getCameraInfo(i, info);
                if (info.facing == targetFacing) {
                    camId = i;
                    break;
                }
            }

            camera = Camera.open(camId);
            Camera.Parameters params = camera.getParameters();

            List<Camera.Size> sizes = params.getSupportedPreviewSizes();
            int bestW = 640;
            int bestH = 480;
            int bestDiff = Integer.MAX_VALUE;
            if (sizes != null) {
                for (Camera.Size s : sizes) {
                    int diff = Math.abs(s.width - 640) + Math.abs(s.height - 480);
                    if (diff < bestDiff) {
                        bestDiff = diff;
                        bestW = s.width;
                        bestH = s.height;
                    }
                }
            }
            params.setPreviewSize(bestW, bestH);
            params.setPreviewFormat(ImageFormat.NV21);
            List<String> focusModes = params.getSupportedFocusModes();
            if (focusModes != null && focusModes.contains(Camera.Parameters.FOCUS_MODE_CONTINUOUS_VIDEO)) {
                params.setFocusMode(Camera.Parameters.FOCUS_MODE_CONTINUOUS_VIDEO);
            }
            camera.setParameters(params);

            dummySurface = new SurfaceTexture(10);
            camera.setPreviewTexture(dummySurface);

            final int previewW = bestW;
            final int previewH = bestH;

            camera.setPreviewCallback(new Camera.PreviewCallback() {
                @Override
                public void onPreviewFrame(final byte[] data, Camera cam) {
                    if (isProcessingFrame || data == null || !cameraEnabled || !isStreaming) return;
                    isProcessingFrame = true;
                    processCameraFrame(data, previewW, previewH);
                    isProcessingFrame = false;
                }
            });

            camera.startPreview();
            lastCameraFrameMs = System.currentTimeMillis();
        } catch (Exception e) {
            camera = null;
        }
    }

    private static synchronized void stopCamera() {
        if (camera != null) {
            try {
                camera.setPreviewCallback(null);
                camera.stopPreview();
                camera.release();
            } catch (Exception ignored) {}
            camera = null;
        }
        if (dummySurface != null) {
            try { dummySurface.release(); } catch (Exception ignored) {}
            dummySurface = null;
        }
    }

    private static synchronized void restartCamera() {
        stopCamera();
        if (cameraEnabled && isStreaming) startCamera();
    }

    private static void processCameraFrame(byte[] nv21, int width, int height) {
        try {
            lastCameraFrameMs = System.currentTimeMillis();
            YuvImage yuv = new YuvImage(nv21, ImageFormat.NV21, width, height, null);
            ByteArrayOutputStream rawJpeg = new ByteArrayOutputStream();
            yuv.compressToJpeg(new Rect(0, 0, width, height), 82, rawJpeg);
            byte[] bytes = rawJpeg.toByteArray();
            Bitmap rawBmp = BitmapFactory.decodeByteArray(bytes, 0, bytes.length);
            if (rawBmp == null) return;

            Matrix m = new Matrix();
            if (useFrontCamera) {
                m.postRotate(270);
                m.postScale(-1f, 1f);
            } else {
                m.postRotate(90);
            }
            Bitmap oriented = Bitmap.createBitmap(rawBmp, 0, 0, rawBmp.getWidth(), rawBmp.getHeight(), m, true);
            rawBmp.recycle();

            latestPreviewBitmap = oriented;
            mainHandler.post(new Runnable() {
                @Override
                public void run() {
                    MainActivity act = activeActivity;
                    if (act != null && act.cameraPreviewView != null) {
                        act.cameraPreviewView.invalidate();
                    }
                }
            });

            ByteArrayOutputStream outJpeg = new ByteArrayOutputStream();
            oriented.compress(Bitmap.CompressFormat.JPEG, 78, outJpeg);
            latestVideoJpeg = outJpeg.toByteArray();
            latestVideoSeq++;
        } catch (Exception ignored) {}
    }

    // ==================== OFFLINE ZERO-INTERNET DISCOVERY & WIRELESS SERVER ====================

    public static void ensureBackgroundServersFromService(Context ctx) {
        if (appContext == null && ctx != null) {
            appContext = ctx.getApplicationContext();
        }
        ensureWirelessServerRunning();
        ensureOfflineDiscoveryRunning();
    }

    private static byte[] buildDiscoveryBeaconBytes() {
        try {
            phoneWifiIp = detectWifiIp(appContext);
            JSONObject obj = new JSONObject();
            obj.put("type", "MMIC_PHONE_BEACON");
            obj.put("device", getFriendlyDeviceName());
            obj.put("model", Build.MODEL != null ? Build.MODEL : "Motorola Edge 50 Neo");
            obj.put("manufacturer", Build.MANUFACTURER != null ? Build.MANUFACTURER : "Motorola");
            obj.put("android", "Android " + Build.VERSION.RELEASE);
            obj.put("ip", phoneWifiIp);
            obj.put("port", WIRELESS_SERVER_PORT);
            obj.put("netMode", wirelessNetMode);
            obj.put("streaming", isStreaming);
            return ("MMIC_DISCOVER:" + obj.toString()).getBytes("UTF-8");
        } catch (Exception e) {
            return "MMIC_DISCOVER".getBytes();
        }
    }

    private static synchronized void ensureOfflineDiscoveryRunning() {
        if (discoveryUdpSocket != null && !discoveryUdpSocket.isClosed()) {
            return;
        }
        Thread discThread = new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    DatagramSocket ds = new DatagramSocket(null);
                    ds.setReuseAddress(true);
                    ds.setBroadcast(true);
                    ds.bind(new InetSocketAddress("0.0.0.0", DISCOVERY_PORT));
                    ds.setSoTimeout(1500);
                    discoveryUdpSocket = ds;

                    byte[] recvBuf = new byte[4096];
                    long lastBeaconMs = 0L;

                    while (!Thread.currentThread().isInterrupted() && !ds.isClosed()) {
                        long now = System.currentTimeMillis();
                        if (now - lastBeaconMs >= 1800L) {
                            lastBeaconMs = now;
                            byte[] beacon = buildDiscoveryBeaconBytes();
                            for (InetAddress bcast : getLocalBroadcastAddresses()) {
                                try {
                                    DatagramPacket dp = new DatagramPacket(beacon, beacon.length, bcast, DISCOVERY_PORT);
                                    ds.send(dp);
                                } catch (Exception ignored) {}
                            }
                        }

                        try {
                            DatagramPacket pkt = new DatagramPacket(recvBuf, recvBuf.length);
                            ds.receive(pkt);
                            String msg = new String(pkt.getData(), 0, pkt.getLength(), "UTF-8");
                            InetAddress senderAddr = pkt.getAddress();
                            String senderIp = senderAddr != null ? senderAddr.getHostAddress() : "";

                            if (msg.startsWith("MMIC_PC_SCAN") || msg.contains("\"type\":\"MMIC_PC\"")) {
                                // Reply directly to the scanning PC with our phone identity & IP
                                byte[] reply = buildDiscoveryBeaconBytes();
                                DatagramPacket repPkt = new DatagramPacket(reply, reply.length, senderAddr, pkt.getPort());
                                ds.send(repPkt);

                                if (msg.contains("{")) {
                                    String jsonPart = msg.substring(msg.indexOf("{"));
                                    JSONObject pcJson = new JSONObject(jsonPart);
                                    String hName = pcJson.optString("hostname", "");
                                    String pcIp = pcJson.optString("ip", senderIp);
                                    if (!hName.isEmpty()) connectedPcName = hName;
                                    if (!pcIp.isEmpty()) connectedPcIp = pcIp;
                                }

                                // Also if we are streaming and not yet connected, proactively connect to the PC!
                                if (isStreaming && !isConnected && acceptedWirelessSocket == null && !senderIp.isEmpty() && !senderIp.equals(phoneWifiIp)) {
                                    try {
                                        Socket outSock = new Socket();
                                        outSock.setTcpNoDelay(true);
                                        outSock.connect(new InetSocketAddress(senderIp, PC_SERVER_PORT), 1200);
                                        acceptedWirelessSocket = outSock;
                                    } catch (Exception ignored) {}
                                }
                            }
                        } catch (Exception timeoutOrIgnored) {}
                    }
                } catch (Exception ignored) {}
            }
        }, "OfflineDiscoveryUDP");
        discThread.setDaemon(true);
        discThread.start();
    }

    private static synchronized void ensureWirelessServerRunning() {
        if (wirelessServerSocket != null && !wirelessServerSocket.isClosed()) {
            return;
        }
        Thread serverThread = new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    synchronized (MainActivity.class) {
                        if (wirelessServerSocket != null) {
                            try { wirelessServerSocket.close(); } catch (Exception ignored) {}
                            wirelessServerSocket = null;
                        }
                        ServerSocket ss = new ServerSocket();
                        ss.setReuseAddress(true);
                        ss.bind(new InetSocketAddress("0.0.0.0", WIRELESS_SERVER_PORT));
                        wirelessServerSocket = ss;
                    }
                    while (!Thread.currentThread().isInterrupted() && wirelessServerSocket != null && !wirelessServerSocket.isClosed()) {
                        try {
                            Socket client = wirelessServerSocket.accept();
                            client.setTcpNoDelay(true);
                            if (acceptedWirelessSocket != null) {
                                try { acceptedWirelessSocket.close(); } catch (Exception ignored) {}
                            }
                            acceptedWirelessSocket = client;
                            if (userManuallyStopped && !isStreaming) {
                                // Listen for explicit remote start/exit commands without auto-starting
                                startRemoteCommandReader(client);
                                continue;
                            }
                            if (!isStreaming && !userManuallyStopped) {
                                mainHandler.post(new Runnable() {
                                    @Override
                                    public void run() {
                                        startStreaming();
                                    }
                                });
                            }
                        } catch (Exception acceptErr) {
                            if (wirelessServerSocket == null || wirelessServerSocket.isClosed()) break;
                        }
                    }
                } catch (Exception ignored) {}
            }
        }, "WirelessServer8988");
        serverThread.setDaemon(true);
        serverThread.start();
    }

    private static synchronized void startForegroundBgService() {
        try {
            if (appContext != null) {
                Intent svc = new Intent(appContext, MotoMicService.class);
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                    appContext.startForegroundService(svc);
                } else {
                    appContext.startService(svc);
                }
            }
        } catch (Exception ignored) {}
    }

    private static synchronized void stopForegroundBgService() {
        try {
            if (appContext != null) {
                Intent svc = new Intent(appContext, MotoMicService.class);
                appContext.stopService(svc);
            }
        } catch (Exception ignored) {}
    }

    private static synchronized void startStreaming() {
        if (isStreaming && streamerThread != null && streamerThread.isAlive()) {
            MainActivity act = activeActivity;
            if (act != null) {
                act.updateControlButtons();
                act.refreshUi();
            }
            return;
        }
        isStreaming = true;
        userManuallyStopped = false;
        final int myToken = ++streamToken;
        peakDb = -60.0f;
        phoneWifiIp = detectWifiIp(appContext);
        startForegroundBgService();
        if (cameraEnabled) startCamera();
        connectionStatus = "Active on " + phoneWifiIp + ":" + WIRELESS_SERVER_PORT + " (" + wirelessNetMode + ")";
        MainActivity act = activeActivity;
        if (act != null) {
            act.updateControlButtons();
            act.refreshUi();
        }

        streamerThread = new Thread(new Runnable() {
            @Override
            public void run() {
                streamingLoop(myToken);
            }
        }, "MotoMicCamStreamer-" + myToken);
        streamerThread.start();
    }

    private static synchronized void stopStreaming() {
        isStreaming = false;
        streamToken++;
        isConnected = false;
        connectionStatus = "Stopped — Tap button below to Start";
        currentDb = -60.0f;
        for (int i = 0; i < waveform.length; i++) waveform[i] = 0f;
        stopCamera();
        stopForegroundBgService();
        if (acceptedWirelessSocket != null) {
            try { acceptedWirelessSocket.close(); } catch (Exception ignored) {}
            acceptedWirelessSocket = null;
        }
        if (currentActiveSocket != null) {
            try { currentActiveSocket.close(); } catch (Exception ignored) {}
            currentActiveSocket = null;
        }
        if (streamerThread != null) {
            streamerThread.interrupt();
            streamerThread = null;
        }
        MainActivity act = activeActivity;
        if (act != null) {
            act.updateControlButtons();
            act.refreshUi();
        }
    }

    private static Object[] obtainActiveSocket() {
        for (int wait = 0; wait < 20; wait++) {
            if (!isStreaming) return null;
            if (acceptedWirelessSocket != null && acceptedWirelessSocket.isConnected() && !acceptedWirelessSocket.isClosed()) {
                Socket s = acceptedWirelessSocket;
                acceptedWirelessSocket = null;
                String remoteIp = s.getInetAddress() != null ? s.getInetAddress().getHostAddress() : "PC";
                connectedPcIp = remoteIp;
                boolean isUsbForward = "127.0.0.1".equals(remoteIp);
                String label = isUsbForward ? "🔌 USB Cable Bridge" : (wirelessNetMode + " (" + remoteIp + ")");
                return new Object[]{s, label};
            }
            try { Thread.sleep(100); } catch (InterruptedException ie) { return null; }
        }
        return null;
    }

    private static byte[] buildHeader(byte type, byte channels, int sr, int seq, int payloadLen) {
        ByteBuffer bb = ByteBuffer.allocate(16).order(ByteOrder.BIG_ENDIAN);
        bb.put((byte) 'M');
        bb.put((byte) 'M');
        bb.put((byte) 'I');
        bb.put((byte) 'C');
        bb.put(type);
        bb.put(channels);
        bb.putShort((short) (sr / 100));
        bb.putInt(seq);
        bb.putInt(payloadLen);
        return bb.array();
    }

    private static void startRemoteCommandReader(final Socket sock) {
        Thread reader = new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    InputStream in = sock.getInputStream();
                    byte[] hdr = new byte[16];
                    while (!sock.isClosed()) {
                        int got = 0;
                        while (got < 16) {
                            int r = in.read(hdr, got, 16 - got);
                            if (r <= 0) return;
                            got += r;
                        }
                        if (hdr[0] != 'M' || hdr[1] != 'M' || hdr[2] != 'I' || hdr[3] != 'C') continue;
                        byte type = hdr[4];
                        int len = ByteBuffer.wrap(hdr, 12, 4).order(ByteOrder.BIG_ENDIAN).getInt();
                        if (len <= 0 || len > 65536) continue;
                        byte[] payload = new byte[len];
                        int pGot = 0;
                        while (pGot < len) {
                            int r = in.read(payload, pGot, len - pGot);
                            if (r <= 0) return;
                            pGot += r;
                        }
                        if (type == PACKET_CONTROL) {
                            JSONObject cmd = new JSONObject(new String(payload, "UTF-8"));
                            String action = cmd.optString("cmd", "");
                            if ("pc_info".equals(action)) {
                                String pName = cmd.optString("pcName", "");
                                String pIp = cmd.optString("pcIp", "");
                                if (!pName.isEmpty()) connectedPcName = pName;
                                if (!pIp.isEmpty()) connectedPcIp = pIp;
                                refreshUiStatic();
                            } else if ("app_mode".equals(action)) {
                                final String m = cmd.optString("value", "WEBCAM_MIC");
                                mainHandler.post(new Runnable() {
                                    @Override
                                    public void run() {
                                        setAppUsageMode(m, false);
                                    }
                                });
                            } else if ("camera_source".equals(action)) {
                                final String src = cmd.optString("value", "PHONE");
                                mainHandler.post(new Runnable() {
                                    @Override
                                    public void run() {
                                        setCameraSourceChoice(src, false);
                                    }
                                });
                            } else if ("flip_camera".equals(action)) {
                                useFrontCamera = !useFrontCamera;
                                mainHandler.post(new Runnable() {
                                    @Override
                                    public void run() {
                                        restartCamera();
                                        MainActivity act = activeActivity;
                                        if (act != null) act.updateControlButtons();
                                    }
                                });
                            } else if ("set_streaming".equals(action)) {
                                final boolean run = cmd.optBoolean("value", true);
                                mainHandler.post(new Runnable() {
                                    @Override
                                    public void run() {
                                        if (run) {
                                            userManuallyStopped = false;
                                            startStreaming();
                                        } else {
                                            userManuallyStopped = true;
                                            stopStreaming();
                                        }
                                    }
                                });
                            } else if ("exit_app".equals(action)) {
                                mainHandler.post(new Runnable() {
                                    @Override
                                    public void run() {
                                        exitAppFromNotification();
                                    }
                                });
                            }
                        }
                    }
                } catch (Exception ignored) {}
            }
        }, "RemoteControlReader");
        reader.setDaemon(true);
        reader.start();
    }

    private static void streamingLoop(final int myToken) {
        int sr = sampleRate;
        int minBuf = AudioRecord.getMinBufferSize(
                sr, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT
        );
        if (minBuf <= 0) minBuf = sr / 10;

        int frameSamples = sr / 50; // 20ms
        AudioRecord recorder = null;
        AcousticEchoCanceler aec = null;
        NoiseSuppressor ns = null;
        try {
            recorder = new AudioRecord(
                    MediaRecorder.AudioSource.VOICE_COMMUNICATION,
                    sr,
                    AudioFormat.CHANNEL_IN_MONO,
                    AudioFormat.ENCODING_PCM_16BIT,
                    Math.max(minBuf * 2, frameSamples * 4)
            );
            if (recorder.getState() != AudioRecord.STATE_INITIALIZED) {
                recorder.release();
                recorder = new AudioRecord(
                        MediaRecorder.AudioSource.MIC,
                        sr,
                        AudioFormat.CHANNEL_IN_MONO,
                        AudioFormat.ENCODING_PCM_16BIT,
                        Math.max(minBuf * 2, frameSamples * 4)
                );
            }
            if (recorder.getState() == AudioRecord.STATE_INITIALIZED) {
                int sessionId = recorder.getAudioSessionId();
                try {
                    if (AcousticEchoCanceler.isAvailable()) {
                        aec = AcousticEchoCanceler.create(sessionId);
                        if (aec != null) aec.setEnabled(true);
                    }
                } catch (Exception ignored) {}
                try {
                    if (NoiseSuppressor.isAvailable()) {
                        ns = NoiseSuppressor.create(sessionId);
                        if (ns != null) ns.setEnabled(true);
                    }
                } catch (Exception ignored) {}
                recorder.startRecording();
            }
        } catch (Exception e) {
            recorder = null;
        }

        short[] pcmBuf = new short[frameSamples];
        ByteBuffer outBytes = ByteBuffer.allocate(frameSamples * 2).order(ByteOrder.LITTLE_ENDIAN);
        int seq = 0;
        long lastSentVideoSeq = -1L;

        try {
            while (isStreaming && streamToken == myToken && !Thread.currentThread().isInterrupted()) {
                isConnected = false;
                phoneWifiIp = detectWifiIp(appContext);
                connectionStatus = "Active (" + phoneWifiIp + ":" + WIRELESS_SERVER_PORT + ") — " + wirelessNetMode;
                refreshUiStatic();

                Object[] connInfo = obtainActiveSocket();
                if (connInfo == null) {
                    try { Thread.sleep(250); } catch (InterruptedException ie) { break; }
                    continue;
                }

                Socket sock = (Socket) connInfo[0];
                currentActiveSocket = sock;
                String transportLabel = (String) connInfo[1];

                try {
                    OutputStream out = sock.getOutputStream();
                    startRemoteCommandReader(sock);

                    JSONObject hs = new JSONObject();
                    hs.put("device", getFriendlyDeviceName());
                    hs.put("model", Build.MODEL != null ? Build.MODEL : "Motorola Edge 50 Neo");
                    hs.put("android", "Android " + Build.VERSION.RELEASE);
                    hs.put("sampleRate", sr);
                    hs.put("source", appMode);
                    hs.put("wifiIp", phoneWifiIp);
                    hs.put("netMode", wirelessNetMode);
                    byte[] hsBytes = hs.toString().getBytes("UTF-8");
                    byte[] hsHeader = buildHeader(PACKET_HANDSHAKE, (byte) 1, sr, 0, hsBytes.length);
                    out.write(hsHeader);
                    out.write(hsBytes);
                    out.flush();

                    isConnected = true;
                    connectionStatus = "LIVE on " + transportLabel;
                    refreshUiStatic();

                    long lastUiUpdate = 0;

                    while (isStreaming && streamToken == myToken && !Thread.currentThread().isInterrupted()) {
                        if (acceptedWirelessSocket != null) break;

                        int readCount = 0;
                        if (!isMuted && recorder != null && recorder.getRecordingState() == AudioRecord.RECORDSTATE_RECORDING) {
                            readCount = recorder.read(pcmBuf, 0, frameSamples);
                        }
                        if (readCount <= 0) {
                            for (int i = 0; i < frameSamples; i++) pcmBuf[i] = 0;
                            Thread.sleep(18);
                            readCount = frameSamples;
                        }

                        outBytes.clear();
                        double sumSq = 0.0;
                        int step = Math.max(1, readCount / 64);

                        for (int i = 0; i < readCount; i++) {
                            float sample = isMuted ? 0f : (pcmBuf[i] * gain);
                            if (sample > 32767f) sample = 32767f;
                            if (sample < -32767f) sample = -32767f;
                            short sVal = (short) sample;
                            outBytes.putShort(sVal);

                            double norm = sVal / 32768.0;
                            sumSq += norm * norm;
                            int wIdx = i / step;
                            if (wIdx < 64) waveform[wIdx] = (float) norm;
                        }

                        float rms = (float) Math.sqrt(sumSq / readCount);
                        float db = (float) (20.0 * Math.log10(Math.max(rms, 1e-6)));
                        if (db < -60f) db = -60f;
                        if (db > 0f) db = 0f;
                        currentDb = db;
                        if (db > peakDb) peakDb = db;

                        int payloadLen = readCount * 2;
                        byte[] audioHdr = buildHeader(PACKET_AUDIO, (byte) 1, sr, seq++, payloadLen);
                        out.write(audioHdr);
                        out.write(outBytes.array(), 0, payloadLen);

                        byte[] vidJpeg = latestVideoJpeg;
                        long vidSeq = latestVideoSeq;
                        if (cameraEnabled && vidJpeg != null && vidSeq != lastSentVideoSeq) {
                            lastSentVideoSeq = vidSeq;
                            byte[] vidHdr = buildHeader(PACKET_VIDEO, (byte) 0, sr, (int) vidSeq, vidJpeg.length);
                            out.write(vidHdr);
                            out.write(vidJpeg);
                            videoFramesSent++;
                        }

                        out.flush();
                        packetsSent = seq;

                        long now = System.currentTimeMillis();
                        if (cameraEnabled && (now - lastCameraFrameMs > 3000)) {
                            lastCameraFrameMs = now;
                            mainHandler.post(new Runnable() {
                                @Override
                                public void run() {
                                    restartCamera();
                                }
                            });
                        }
                        if (now - lastUiUpdate >= 60) {
                            lastUiUpdate = now;
                            refreshUiStatic();
                        }
                    }
                } catch (InterruptedException ie) {
                    break;
                } catch (Exception e) {
                    // Reconnect socket without stopping stream
                } finally {
                    try { sock.close(); } catch (Exception ignored) {}
                    if (currentActiveSocket == sock) currentActiveSocket = null;
                }
            }
        } finally {
            if (aec != null) {
                try { aec.release(); } catch (Exception ignored) {}
            }
            if (ns != null) {
                try { ns.release(); } catch (Exception ignored) {}
            }
            if (recorder != null) {
                try {
                    recorder.stop();
                    recorder.release();
                } catch (Exception ignored) {}
            }
        }
    }

    @Override
    protected void onDestroy() {
        // Do NOT stop streaming when user switches to another app or locks screen!
        // MotoMicService keeps the microphone and camera stream alive in the background.
        if (activeActivity == this) {
            activeActivity = null;
        }
        super.onDestroy();
    }

    // ==================== CUSTOM VIEWS ====================

    private class CameraPreviewView extends View {
        private final Paint bgPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
        private final Paint bmpPaint = new Paint(Paint.ANTI_ALIAS_FLAG | Paint.FILTER_BITMAP_FLAG);
        private final Paint textPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
        private final RectF dstRect = new RectF();

        public CameraPreviewView(Context ctx) {
            super(ctx);
            bgPaint.setColor(Color.parseColor("#050811"));
            textPaint.setColor(Color.parseColor("#94A3B8"));
            textPaint.setTextSize(dp(14));
            textPaint.setTextAlign(Paint.Align.CENTER);
        }

        @Override
        protected void onDraw(Canvas canvas) {
            super.onDraw(canvas);
            int w = getWidth();
            int h = getHeight();
            dstRect.set(0, 0, w, h);
            canvas.drawRoundRect(dstRect, dp(12), dp(12), bgPaint);

            Bitmap bmp = latestPreviewBitmap;
            if (isStreaming && cameraEnabled && bmp != null && !bmp.isRecycled()) {
                float scale = Math.min((float) w / bmp.getWidth(), (float) h / bmp.getHeight());
                float dw = bmp.getWidth() * scale;
                float dh = bmp.getHeight() * scale;
                float left = (w - dw) / 2f;
                float top = (h - dh) / 2f;
                dstRect.set(left, top, left + dw, top + dh);
                canvas.drawBitmap(bmp, null, dstRect, bmpPaint);
            } else {
                String msg = !isStreaming
                        ? "⚪ Stream Stopped — Tap button below to Start"
                        : (cameraEnabled ? "Starting Camera..." : "🎙️ Mic Only Mode Active");
                canvas.drawText(msg, w / 2f, h / 2f, textPaint);
            }

            if (isStreaming) {
                Paint pillPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
                pillPaint.setColor(Color.parseColor("#CC0F172A"));
                RectF pill = new RectF(w - dp(92), dp(10), w - dp(10), dp(36));
                canvas.drawRoundRect(pill, dp(13), dp(13), pillPaint);

                Paint dotPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
                dotPaint.setColor(Color.parseColor(blinkDotOn ? "#EF4444" : "#334155"));
                canvas.drawCircle(w - dp(76), dp(23), dp(6), dotPaint);

                Paint lblPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
                lblPaint.setColor(Color.WHITE);
                lblPaint.setTextSize(dp(11));
                lblPaint.setTypeface(Typeface.DEFAULT_BOLD);
                canvas.drawText("LIVE ON", w - dp(64), dp(27), lblPaint);
            }
        }
    }

    private class WaveformView extends View {
        private final Paint bgPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
        private final Paint wavePaint = new Paint(Paint.ANTI_ALIAS_FLAG);
        private final Paint vuPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
        private final RectF rect = new RectF();

        public WaveformView(Context ctx) {
            super(ctx);
            bgPaint.setColor(Color.parseColor("#050811"));
            wavePaint.setStrokeWidth(dp(3));
            wavePaint.setStrokeCap(Paint.Cap.ROUND);
        }

        @Override
        protected void onDraw(Canvas canvas) {
            super.onDraw(canvas);
            int w = getWidth();
            int h = getHeight();
            int waveH = h - dp(12);

            rect.set(0, 0, w, waveH);
            canvas.drawRoundRect(rect, dp(8), dp(8), bgPaint);

            float midY = waveH / 2f;
            wavePaint.setColor((isConnected && isStreaming) ? Color.parseColor("#22D3EE") : Color.parseColor("#475569"));
            float barW = (float) w / waveform.length;
            for (int i = 0; i < waveform.length; i++) {
                float amp = Math.max(dp(2), Math.min(midY - dp(3), Math.abs(waveform[i]) * midY * 2.2f));
                float x = i * barW + barW / 2f;
                canvas.drawLine(x, midY - amp, x, midY + amp, wavePaint);
            }

            int vuTop = waveH + dp(4);
            rect.set(0, vuTop, w, h);
            canvas.drawRoundRect(rect, dp(4), dp(4), bgPaint);

            float norm = (currentDb + 60f) / 60f;
            if (norm < 0f) norm = 0f;
            if (norm > 1f) norm = 1f;
            if (currentDb > -6f) vuPaint.setColor(Color.parseColor("#EF4444"));
            else if (currentDb > -18f) vuPaint.setColor(Color.parseColor("#F59E0B"));
            else vuPaint.setColor(Color.parseColor("#10B981"));
            rect.set(0, vuTop, w * norm, h);
            canvas.drawRoundRect(rect, dp(4), dp(4), vuPaint);
        }
    }
}
