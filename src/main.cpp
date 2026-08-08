#include "ofMain.h"
#include "ofApp.h"
#include "ofAppGLFWWindow.h"
#include <GLFW/glfw3.h>

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
void logMonitors(int selected){
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

	ofLogNotice("main") << "Using monitor index " << selected;
}

//========================================================================
int main( ){

	// default channel's lazy initialiser never runs on this toolchain - see Probe 1/2
	ofSetLoggerChannel(std::make_shared<ofConsoleLoggerChannel>());

	int monitorIndex = readMonitorIndex();
	logMonitors(monitorIndex);

	ofGLFWWindowSettings settings;
	settings.setSize(1920, 1080);
	settings.windowMode = OF_FULLSCREEN;
	settings.monitor = monitorIndex;

	auto window = ofCreateWindow(settings);

	ofRunApp(window, std::make_shared<ofApp>());
	ofRunMainLoop();

}
