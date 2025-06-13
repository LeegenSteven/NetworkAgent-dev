import 'dart:convert';
import 'package:http/http.dart' as http;
import 'package:networkagent/models/agent.dart';
import 'package:networkagent/utils/environment_config.dart' as config;

class APIService{
  Map<String, String> getRequestHeaders = {
    'Accept': 'application/json',
    'Access-Control-Request-Method': 'GET',
    'Origin': '*',
    'Access-Control-Allow-Origin': '*'
  };

  Map<String, String> postRequestHeaders = {
    'Content-type': 'application/json',
    'Accept': 'application/json',
    'Access-Control-Request-Method': 'POST',
    'Origin': '*',
    'Access-Control-Allow-Origin': '*'
  };

  Future<List<Agent>> listAgents() async {
    List<Agent> agents = [];
    try {
      var agent_url = Uri.parse('${config.EnvironmentConfig.agentUrl}/listagents');
      print('Fetching agents from: $agent_url');
      
      try {
        final http.Response response = await http.get(agent_url, headers: getRequestHeaders);
        print('Response status code: ${response.statusCode}');
        
        if (response.statusCode == 200) {
          print('Response body: ${response.body}');
          
          try {
            final dynamic decodedData = jsonDecode(response.body);
            print('Decoded data type: ${decodedData.runtimeType}');
            
            if (decodedData is List) {
              final List<dynamic> agentsList = decodedData;
              print('Agents list length: ${agentsList.length}');
              
              for (var agentData in agentsList) {
                print('Agent data: $agentData');
                if (agentData is Map<String, dynamic>) {
                  try {
                    final agent = Agent.fromJson(agentData);
                    agents.add(agent);
                    print('Successfully added agent: ${agent.name}');
                  } catch (e) {
                    print('Error parsing agent data: $e');
                    print('Agent data that failed: $agentData');
                  }
                } else {
                  print('Agent data is not a Map: ${agentData.runtimeType}');
                }
              }
            } else {
              print('Decoded data is not a List: ${decodedData.runtimeType}');
            }
          } catch (e) {
            print('Error decoding JSON: $e');
          }
        } else {
          print('Failed to load agents: ${response.statusCode}');
          print('Response body: ${response.body}');
        }
      } catch (e) {
        print('HTTP request error: $e');
      }
    } catch (e) {
      print('Error fetching agents: $e');
    }
    
    print('Returning ${agents.length} agents');
    return agents;
  }
  
  Future<Agent?> addAgent(String url) async {
    try {
      var addUrl = Uri.parse('${config.EnvironmentConfig.agentUrl}/addagent');
      print('Adding agent with URL: $url');
      print('Add endpoint: $addUrl');
      
      // Create the request body
      Map<String, String> body = {'url': url};
      
      try {
        final http.Response response = await http.post(
          addUrl,
          headers: postRequestHeaders,
          body: jsonEncode(body)
        );
        print('Response status code: ${response.statusCode}');
        
        if (response.statusCode == 200) {
          print('Response body: ${response.body}');
          
          try {
            final Map<String, dynamic> agentData = jsonDecode(response.body);
            print('Agent data: $agentData');
            
            final agent = Agent.fromJson(agentData);
            print('Successfully added agent: ${agent.name}');
            return agent;
          } catch (e) {
            print('Error parsing agent data: $e');
          }
        } else {
          print('Failed to add agent: ${response.statusCode}');
          print('Response body: ${response.body}');
        }
      } catch (e) {
        print('HTTP request error: $e');
      }
    } catch (e) {
      print('Error adding agent: $e');
    }
    
    return null;
  }

  Future<List<Agent>> deleteAgent(String url) async {
    List<Agent> agents = [];
    try {
      var deleteUrl = Uri.parse('${config.EnvironmentConfig.agentUrl}/deleteagent');
      print('Deleting agent with URL: $url');
      print('Delete endpoint: $deleteUrl');
      
      // Create the request body
      Map<String, String> body = {'url': url};
      
      try {
        final http.Response response = await http.post(
          deleteUrl,
          headers: postRequestHeaders,
          body: jsonEncode(body)
        );
        print('Response status code: ${response.statusCode}');
        
        if (response.statusCode == 200) {
          print('Response body: ${response.body}');
          
          try {
            final dynamic decodedData = jsonDecode(response.body);
            print('Decoded data type: ${decodedData.runtimeType}');
            
            if (decodedData is List) {
              final List<dynamic> agentsList = decodedData;
              print('Updated agents list length: ${agentsList.length}');
              
              for (var agentData in agentsList) {
                if (agentData is Map<String, dynamic>) {
                  try {
                    final agent = Agent.fromJson(agentData);
                    agents.add(agent);
                    print('Agent in updated list: ${agent.name}');
                  } catch (e) {
                    print('Error parsing agent data: $e');
                  }
                }
              }
            } else {
              print('Decoded data is not a List: ${decodedData.runtimeType}');
            }
          } catch (e) {
            print('Error decoding JSON: $e');
          }
        } else {
          print('Failed to delete agent: ${response.statusCode}');
          print('Response body: ${response.body}');
        }
      } catch (e) {
        print('HTTP request error: $e');
      }
    } catch (e) {
      print('Error deleting agent: $e');
    }
    
    print('Returning ${agents.length} agents after deletion');
    return agents;
  }

  Future<String> getNodeDetails(String nodeId) async {
    try {
      var node_url = Uri.parse('${config.EnvironmentConfig.agentUrl}/node/$nodeId');
      print('Fetching node details from: $node_url');
      
      final http.Response response = await http.get(node_url, headers: getRequestHeaders);
      
      if (response.statusCode == 200) {
        final dynamic decodedData = jsonDecode(response.body);
        if (decodedData is Map<String, dynamic> && decodedData.containsKey('summary')) {
          return decodedData['summary'];
        } else if (decodedData is String) {
          return decodedData;
        }
        else {
          throw Exception('Failed to parse node details summary');
        }
      } else {
        throw Exception('Failed to load node details');
      }
    } catch (e) {
      print('Error fetching node details: $e');
      rethrow;
    }
  }
}
