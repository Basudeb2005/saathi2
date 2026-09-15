package ai.saathi.console;

import android.Manifest;
import android.bluetooth.BluetoothAdapter;
import android.bluetooth.BluetoothDevice;
import android.bluetooth.BluetoothSocket;
import android.content.Context;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.View;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONObject;

import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * One screen: find the Pi, then get out of the way.
 *
 * <p>The problem this exists for is circular. To open the Pi's web console
 * you need its address; to learn its address you need to reach it; to
 * reach it you need the network it may not be on. Bluetooth is the way
 * out, because it is a second radio that doesn't care about any of that —
 * pair once, ask, and you have the address and the key together.
 *
 * <p>After that first time this is a WebView and nothing more. The real
 * interface is the page the Pi serves, which means fixing the console
 * never means shipping a new APK.
 */
public class MainActivity extends android.app.Activity {

    /** The standard Serial Port Profile UUID — what the Pi advertises. */
    private static final UUID SPP = UUID.fromString("00001101-0000-1000-8000-00805F9B34FB");
    private static final String PREFS = "saathi";
    private static final int ASK_BLUETOOTH = 1;

    private final ExecutorService pool = Executors.newSingleThreadExecutor();
    private final Handler ui = new Handler(Looper.getMainLooper());

    private WebView web;
    private View finder;
    private TextView message;

    @Override
    protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        setContentView(R.layout.activity_main);

        web = findViewById(R.id.web);
        finder = findViewById(R.id.finder);
        message = findViewById(R.id.message);

        WebSettings settings = web.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);   // the page keeps the token here
        web.setWebViewClient(new WebViewClient());

        findViewById(R.id.find).setOnClickListener(v -> findOverBluetooth());
        findViewById(R.id.open).setOnClickListener(v -> {
            String typed = ((EditText) findViewById(R.id.manual)).getText().toString().trim();
            if (!typed.isEmpty()) {
                if (!typed.startsWith("http")) typed = "http://" + typed;
                if (!typed.matches(".*:\\d+.*")) typed = typed + ":8765";
                open(typed);
            }
        });

        String known = prefs().getString("url", null);
        if (known != null) open(known);
    }

    private SharedPreferences prefs() {
        return getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    private void say(String text) {
        ui.post(() -> message.setText(text));
    }

    // ---- the web console ------------------------------------------------

    private void open(String url) {
        prefs().edit().putString("url", url).apply();
        ui.post(() -> {
            finder.setVisibility(View.GONE);
            web.setVisibility(View.VISIBLE);
            web.loadUrl(url);
        });
    }

    /** Back goes back through the page before it leaves the app. */
    @Override
    public void onBackPressed() {
        if (web.getVisibility() == View.VISIBLE && web.canGoBack()) {
            web.goBack();
        } else if (web.getVisibility() == View.VISIBLE) {
            // Not finish(): the point of going back from the console is
            // usually that it's the wrong address.
            web.setVisibility(View.GONE);
            finder.setVisibility(View.VISIBLE);
            prefs().edit().remove("url").apply();
        } else {
            super.onBackPressed();
        }
    }

    // ---- bluetooth ------------------------------------------------------

    private void findOverBluetooth() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S
                && checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT)
                != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.BLUETOOTH_CONNECT}, ASK_BLUETOOTH);
            return;
        }
        say("Looking…");
        pool.execute(this::probeBondedDevices);
    }

    @Override
    public void onRequestPermissionsResult(int code, String[] perms, int[] granted) {
        if (code == ASK_BLUETOOTH) {
            if (granted.length > 0 && granted[0] == PackageManager.PERMISSION_GRANTED) {
                findOverBluetooth();
            } else {
                say("Without Bluetooth permission this can't find the Pi on its own. "
                        + "You can still type its address below.");
            }
        }
    }

    /**
     * Try every paired device until one answers like a Saathi.
     *
     * <p>Not a scan: a scan finds everything in the building and asks the
     * user to pick, and the thing they are looking for is by definition
     * the thing they can't identify. Everything already paired is a short
     * list, and asking each one costs a second.
     */
    private void probeBondedDevices() {
        BluetoothAdapter adapter = BluetoothAdapter.getDefaultAdapter();
        if (adapter == null || !adapter.isEnabled()) {
            say("Bluetooth is off. Turn it on and try again.");
            return;
        }

        java.util.Set<BluetoothDevice> paired;
        try {
            paired = adapter.getBondedDevices();
        } catch (SecurityException e) {
            say("Bluetooth permission was refused.");
            return;
        }
        if (paired == null || paired.isEmpty()) {
            say("No paired devices. Pair the Pi in your phone's Bluetooth settings first — "
                    + "it appears under its hostname.");
            return;
        }

        for (BluetoothDevice device : paired) {
            String name;
            try {
                name = device.getName() == null ? device.getAddress() : device.getName();
            } catch (SecurityException e) {
                continue;
            }
            say("Asking " + name + "…");
            String answer = ask(device);
            if (answer == null) continue;
            try {
                JSONObject hello = new JSONObject(answer);
                String ip = hello.optString("ip", "");
                if (ip.isEmpty() || "null".equals(ip)) {
                    say(name + " is there but has no network address yet. "
                            + "Open its Bluetooth terminal and run: hotspot on");
                    return;
                }
                String url = "http://" + ip + ":" + hello.optInt("port", 8765)
                        + "/?token=" + android.net.Uri.encode(hello.optString("token", ""));
                open(url);
                return;
            } catch (Exception ignored) {
                // Answered, but not with our JSON — something else on SPP.
            }
        }
        say("None of the paired devices answered. Is the Pi powered on, and is "
                + "saathi-console running? Check with: systemctl status saathi-console@pi");
    }

    /**
     * Connect, send {@code hello}, read one line of JSON back.
     *
     * <p>{@code hello} rather than scraping {@code status}: the moment
     * anyone improves the wording of the human-readable output, every
     * installed copy of this app stops finding the Pi.
     */
    private String ask(BluetoothDevice device) {
        BluetoothSocket socket = null;
        try {
            socket = device.createRfcommSocketToServiceRecord(SPP);
            socket.connect();

            OutputStream out = socket.getOutputStream();
            InputStream in = socket.getInputStream();
            out.write("hello\n".getBytes("UTF-8"));
            out.flush();

            StringBuilder seen = new StringBuilder();
            long deadline = System.currentTimeMillis() + 6000;
            byte[] buffer = new byte[512];
            while (System.currentTimeMillis() < deadline) {
                int read = in.read(buffer);
                if (read <= 0) break;
                seen.append(new String(buffer, 0, read, "UTF-8"));
                // The banner and prompt come too, so find the JSON rather
                // than assuming the first line is it.
                int start = seen.indexOf("{");
                int end = seen.lastIndexOf("}");
                if (start >= 0 && end > start) {
                    return seen.substring(start, end + 1);
                }
            }
        } catch (Exception ignored) {
            // Wrong device, not paired any more, out of range, no SPP.
        } finally {
            if (socket != null) {
                try { socket.close(); } catch (Exception ignored) { }
            }
        }
        return null;
    }

    /** Unused today, kept because the next thing anyone adds is a scan. */
    @SuppressWarnings("unused")
    private boolean reachable(String url) {
        try {
            HttpURLConnection connection = (HttpURLConnection) new URL(url + "/api/ping").openConnection();
            connection.setConnectTimeout(1500);
            connection.setReadTimeout(1500);
            return connection.getResponseCode() == 200;
        } catch (Exception e) {
            return false;
        }
    }
}
