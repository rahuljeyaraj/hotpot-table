#include "ofMain.h"
#include "ofApp.h"
#include "ofAppGLFWWindow.h"
#include <GLFW/glfw3.h>

#ifdef _WIN32
#include <windows.h>
#endif

//--------------------------------------------------------------
int readMonitorIndex(){
	std::string path = "display.txt";

	if(!ofFile::doesFileExist(path)){
		ofFile out(path, ofFile::WriteOnly);
		out << "0";
		out.close();
		return 0;
	}

	ofBuffer buffer = ofBufferFromFile(path);
	return ofToInt(*buffer.getLines().begin());
}

//--------------------------------------------------------------
// Logs every monitor and returns the desktop origin of the selected one.
glm::ivec2 logMonitors(int & selected){
	glfwInit();

	int count = 0;
	GLFWmonitor ** monitors = glfwGetMonitors(&count);

	ofLogNotice("main") << "Detected " << count << " monitor(s):";
	for(int i = 0; i < count; i++){
		const GLFWvidmode * mode = glfwGetVideoMode(monitors[i]);
		int x, y;
		glfwGetMonitorPos(monitors[i], &x, &y);
		const char * name = glfwGetMonitorName(monitors[i]);

		ofLogNotice("main") << "  [" << i << "] " << name
			<< " " << mode->width << "x" << mode->height
			<< " @ " << mode->refreshRate << "Hz"
			<< " pos(" << x << "," << y << ")";
	}

	if(count < 1){
		ofLogError("main") << "No monitors reported by GLFW";
		return glm::ivec2(0, 0);
	}

	if(selected < 0 || selected >= count){
		ofLogWarning("main") << "display.txt asks for monitor " << selected
			<< " but only " << count << " present - falling back to 0";
		selected = 0;
	}

	int ox = 0, oy = 0;
	glfwGetMonitorPos(monitors[selected], &ox, &oy);

	ofLogNotice("main") << "Using monitor index " << selected
		<< " origin(" << ox << "," << oy << ")";

	return glm::ivec2(ox, oy);
}

//========================================================================
int main( ){

#ifdef _WIN32
	// No manifest declares this app DPI-aware, so Windows was treating it as
	// unaware and virtualizing it against a system-wide DPI baseline (usually
	// the primary monitor's scale) instead of the actual monitor the
	// fullscreen window lands on. 2026-08-14 rig test: the fluid sim (driven
	// by raw mouse coords) only ever reached a fraction of the screen, and
	// that fraction shrank further on the higher-DPI 2560x1440 monitor —
	// exactly the signature of mouse coordinates (and/or the render surface)
	// being scaled against the wrong monitor's DPI. Must run before any
	// window is created.
	SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
#endif

	// default channel's lazy initialiser never runs on this toolchain - see Probe 1/2
	ofSetLoggerChannel(std::make_shared<ofConsoleLoggerChannel>());

	int monitorIndex = readMonitorIndex();
	glm::ivec2 monitorOrigin = logMonitors(monitorIndex);

	// oF creates a plain windowed window first and defers setFullscreen() to the
	// first frame, where the target monitor is resolved from wherever that window
	// landed (ofAppGLFWWindow::getCurrentMonitor). So place the window on the
	// target monitor ourselves and go fullscreen from ofApp::setup().
	// Inset from the origin: Windows shifts a decorated window a few px off the
	// requested spot (asked for the exact origin, got origin + (-1,-7)), which puts
	// the top-left just OUTSIDE the target monitor's rect. getCurrentMonitor()
	// point-tests that corner and falls back to monitor 0 when it misses.
	const int kCornerInset = 100;

	ofGLFWWindowSettings settings;
	settings.setSize(1920, 1080);
	settings.windowMode = OF_WINDOW;
	settings.monitor = monitorIndex;
	settings.setPosition(ofVec2f(monitorOrigin.x + kCornerInset, monitorOrigin.y + kCornerInset));

	auto window = ofCreateWindow(settings);

	ofLogNotice("main") << "After create: pos("
		<< window->getWindowPosition().x << "," << window->getWindowPosition().y
		<< ") size " << window->getWindowSize().x << "x" << window->getWindowSize().y;

	ofRunApp(window, std::make_shared<ofApp>());
	ofRunMainLoop();

}
