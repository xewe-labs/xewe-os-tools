const fanSpeed = document.getElementById("fanSpeed");
const fanSpeedValue = document.getElementById("fanSpeedValue");
const fanRpm = document.getElementById("fanRpm");
const ledOn = document.getElementById("ledOn");
const ledColor = document.getElementById("ledColor");

function render(state) {
  fanSpeed.value = state.fan.speed;
  fanSpeedValue.textContent = state.fan.speed;
  fanRpm.textContent = state.fan.rpm;
  ledOn.checked = state.led.on;
  ledColor.value = state.led.color;
}

fanSpeed.addEventListener("change", async () => {
  await postJson("/api/fan", { speed: Number(fanSpeed.value) });
  render(await getState());
});

ledOn.addEventListener("change", () => postJson("/api/led", { on: ledOn.checked }));
ledColor.addEventListener("change", () => postJson("/api/led", { color: ledColor.value }));

getState().then(render);
