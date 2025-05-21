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

  Future<List<Agent>?> listAgents() async{
    try{
      var agent_url = Uri.parse('${config.EnvironmentConfig.agentUrl}/listagents');
      final http.Response response = await http.get(agent_url, headers: getRequestHeaders);

      if (response.statusCode == 200){

        Agent _model = Agent.fromJson(jsonDecode(response.body));
      }

    }catch (e){
      print (e);
    }
    return [];
  }
}