package at.ruediway.wear;

import android.app.Activity;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URI;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public final class MainActivity extends Activity {
    private static final int BACKGROUND = Color.rgb(16, 25, 23);
    private static final int FOREGROUND = Color.rgb(242, 248, 244);
    private static final int MUTED = Color.rgb(172, 197, 183);
    private static final int ACCENT = Color.rgb(48, 122, 83);
    private static final long REFRESH_MS = 4000;

    private final Handler handler = new Handler(Looper.getMainLooper());
    private final ExecutorService network = Executors.newSingleThreadExecutor();
    private final Runnable refresh = this::loadResult;
    private SharedPreferences preferences;
    private LinearLayout content;
    private TextView status;
    private TextView answer;
    private EditText serverInput;
    private EditText codeInput;
    private boolean visible;
    private boolean requestRunning;
    private boolean disconnecting;
    private int shownVersion = -1;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        preferences = getSharedPreferences("connection", MODE_PRIVATE);
        showCurrentScreen();
    }

    @Override
    protected void onStart() {
        super.onStart();
        visible = true;
        if (hasSession()) loadResult();
    }

    @Override
    protected void onStop() {
        visible = false;
        handler.removeCallbacks(refresh);
        String base = preferences.getString("server", "");
        String token = preferences.getString("token", "");
        if (!disconnecting && !base.isEmpty() && !token.isEmpty()) {
            network.execute(() -> {
                try { request(base, "/session/presence", "DELETE", token, null); }
                catch (Exception ignored) { }
            });
        }
        super.onStop();
    }

    @Override
    protected void onDestroy() {
        network.shutdown();
        super.onDestroy();
    }

    private boolean hasSession() {
        return !preferences.getString("server", "").isEmpty()
                && !preferences.getString("token", "").isEmpty();
    }

    private void showCurrentScreen() {
        ScrollView scroll = new ScrollView(this);
        scroll.setFillViewport(true);
        scroll.setBackgroundColor(BACKGROUND);
        content = new LinearLayout(this);
        content.setOrientation(LinearLayout.VERTICAL);
        content.setGravity(Gravity.CENTER_HORIZONTAL);
        int side = dp(26);
        content.setPadding(side, dp(28), side, dp(28));
        scroll.addView(content);
        setContentView(scroll);

        label("RuediWay", 22, FOREGROUND);
        status = label("", 13, MUTED);
        if (hasSession()) showAnswerControls();
        else showPairingControls();
    }

    private void showPairingControls() {
        status.setText("Mit dem PC koppeln");
        label("PC-Adresse", 14, FOREGROUND);
        serverInput = new EditText(this);
        serverInput.setSingleLine(true);
        serverInput.setTextColor(FOREGROUND);
        serverInput.setHintTextColor(MUTED);
        serverInput.setHint("192.168.x.x:8000");
        serverInput.setText(preferences.getString("server", "").replace("http://", ""));
        serverInput.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        content.addView(serverInput, fullWidth());

        label("Code vom PC", 14, FOREGROUND);
        codeInput = new EditText(this);
        codeInput.setSingleLine(true);
        codeInput.setTextColor(FOREGROUND);
        codeInput.setHintTextColor(MUTED);
        codeInput.setHint("6 Ziffern");
        codeInput.setInputType(InputType.TYPE_CLASS_NUMBER);
        content.addView(codeInput, fullWidth());
        button("Verbinden", this::pair);
    }

    private void showAnswerControls() {
        status.setText("Verbinde …");
        answer = label("Noch keine Antwort.", 17, FOREGROUND);
        button("Jetzt aktualisieren", this::loadResult);
        button("Antwort löschen", this::clearAnswer);
        button("Neu koppeln", this::disconnectAndPair);
    }

    private void disconnectAndPair() {
        if (disconnecting) return;
        disconnecting = true;
        handler.removeCallbacks(refresh);
        status.setText("Verbindung wird getrennt …");
        String base = preferences.getString("server", "");
        String token = preferences.getString("token", "");
        network.execute(() -> {
            Exception failure = null;
            try {
                request(base, "/session", "DELETE", token, null);
            } catch (Exception error) {
                if (!"Kopplung abgelaufen. Neu koppeln.".equals(error.getMessage())) {
                    failure = error;
                }
            }
            Exception result = failure;
            handler.post(() -> {
                disconnecting = false;
                if (result != null) {
                    status.setText("Trennen fehlgeschlagen. Bitte erneut versuchen.");
                    return;
                }
                preferences.edit().remove("token").remove("cleared_version").apply();
                shownVersion = -1;
                showCurrentScreen();
            });
        });
    }

    private void clearAnswer() {
        if (shownVersion <= 0) {
            answer.setText("Noch keine Antwort.");
            return;
        }
        preferences.edit().putInt("cleared_version", shownVersion).apply();
        answer.setText("Antwort gelöscht. Die nächste Analyse erscheint automatisch.");
    }

    private TextView label(String text, int size, int color) {
        TextView view = new TextView(this);
        view.setText(text);
        view.setTextSize(size);
        view.setTextColor(color);
        view.setGravity(Gravity.CENTER);
        view.setPadding(dp(2), dp(6), dp(2), dp(8));
        content.addView(view, fullWidth());
        return view;
    }

    private void button(String text, Runnable action) {
        Button button = new Button(this);
        button.setText(text);
        button.setTextColor(FOREGROUND);
        button.setBackgroundTintList(android.content.res.ColorStateList.valueOf(ACCENT));
        content.addView(button, fullWidth());
        button.setOnClickListener(view -> action.run());
    }

    private LinearLayout.LayoutParams fullWidth() {
        return new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT);
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    private void pair() {
        String code = codeInput.getText().toString().trim();
        final String base;
        try {
            base = normalizeServer(serverInput.getText().toString());
        } catch (Exception error) {
            status.setText("PC-Adresse prüfen.");
            return;
        }
        if (!code.matches("[0-9]{6}")) {
            status.setText("Bitte 6 Ziffern eingeben.");
            return;
        }
        status.setText("Verbinde …");
        network.execute(() -> {
            try {
                JSONObject payload = new JSONObject().put("code", code);
                JSONObject response = request(base, "/pair", "POST", null, payload);
                String token = response.getString("token");
                preferences.edit().putString("server", base).putString("token", token)
                        .remove("cleared_version").apply();
                handler.post(() -> {
                    if (!visible) return;
                    shownVersion = -1;
                    showCurrentScreen();
                    loadResult();
                });
            } catch (Exception error) {
                handler.post(() -> status.setText(message(error)));
            }
        });
    }

    private void loadResult() {
        handler.removeCallbacks(refresh);
        if (!visible || !hasSession() || disconnecting) return;
        if (requestRunning) {
            handler.postDelayed(refresh, REFRESH_MS);
            return;
        }
        requestRunning = true;
        String base = preferences.getString("server", "");
        String token = preferences.getString("token", "");
        network.execute(() -> {
            try {
                JSONObject result = request(base, "/watch/result", "GET", token, null);
                handler.post(() -> {
                    requestRunning = false;
                    if (!visible || disconnecting || answer == null
                            || !token.equals(preferences.getString("token", ""))) return;
                    int version = result.optInt("version", 0);
                    if (version != shownVersion) {
                        shownVersion = version;
                        String value = result.optString("answer", "");
                        boolean cleared = version == preferences.getInt("cleared_version", -1);
                        answer.setText(cleared ? "Antwort gelöscht. Die nächste Analyse erscheint automatisch."
                                : value.isEmpty() || "null".equals(value) ? "Noch keine Antwort." : value);
                    }
                    status.setText("Verbunden · neue Antworten erscheinen automatisch");
                    handler.postDelayed(refresh, REFRESH_MS);
                });
            } catch (Exception error) {
                handler.post(() -> {
                    requestRunning = false;
                    if (!visible || disconnecting || status == null) return;
                    status.setText(message(error));
                    handler.postDelayed(refresh, REFRESH_MS);
                });
            }
        });
    }

    private String normalizeServer(String input) throws Exception {
        String value = input.trim();
        if (!value.startsWith("http://")) value = "http://" + value;
        URI uri = new URI(value);
        if (!"http".equals(uri.getScheme()) || uri.getHost() == null || uri.getUserInfo() != null
                || uri.getRawQuery() != null || uri.getRawFragment() != null
                || (uri.getRawPath() != null && !uri.getRawPath().isEmpty())) {
            throw new IllegalArgumentException("Ungültige Adresse");
        }
        int port = uri.getPort() == -1 ? 8000 : uri.getPort();
        if (port < 1 || port > 65535) throw new IllegalArgumentException("Ungültiger Port");
        return "http://" + uri.getHost() + ":" + port;
    }

    private JSONObject request(String base, String path, String method, String token, JSONObject payload) throws Exception {
        HttpURLConnection connection = (HttpURLConnection) new URL(base + path).openConnection();
        try {
            connection.setRequestMethod(method);
            connection.setConnectTimeout(5000);
            connection.setReadTimeout(5000);
            connection.setUseCaches(false);
            if (token != null) connection.setRequestProperty("X-Ruediway-Token", token);
            if (payload != null) {
                connection.setDoOutput(true);
                connection.setRequestProperty("Content-Type", "application/json; charset=utf-8");
                byte[] bytes = payload.toString().getBytes(StandardCharsets.UTF_8);
                connection.getOutputStream().write(bytes);
            }
            int statusCode = connection.getResponseCode();
            InputStream stream = statusCode < 400 ? connection.getInputStream() : connection.getErrorStream();
            ByteArrayOutputStream output = new ByteArrayOutputStream();
            if (stream != null) {
                try (InputStream body = stream) {
                    byte[] buffer = new byte[4096];
                    int count;
                    while ((count = body.read(buffer)) != -1) {
                        if (output.size() + count > 1024 * 1024) throw new Exception("Antwort zu groß.");
                        output.write(buffer, 0, count);
                    }
                }
            }
            JSONObject json = new JSONObject(output.toString(StandardCharsets.UTF_8));
            if (statusCode >= 400) {
                if (statusCode == 401) throw new Exception("Kopplung abgelaufen. Neu koppeln.");
                throw new Exception(json.optString("detail", "Serverfehler " + statusCode));
            }
            return json;
        } finally {
            connection.disconnect();
        }
    }

    private String message(Exception error) {
        String value = error.getMessage();
        return value == null || value.isEmpty() ? "PC nicht erreichbar." : value;
    }
}
